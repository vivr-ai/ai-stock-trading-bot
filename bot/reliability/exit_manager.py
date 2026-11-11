"""Stage 6b: exit state machine (Reliability plan v3), behind EXIT_MANAGER_ENABLED.

One exit per position, closed by REPRICING the bracket's take-profit leg to a
marketable limit while the stop leg stays held. Only when that path can't be
used does it fall back to cancel -> confirm -> sell, and a failed sell hands
the position straight back to the protection manager.

Facts this relies on (Stage 6a, real staging account, 9-10 Nov):
  * PATCH /orders/{id} on a working take-profit: 200 'new'; old order becomes
    'replaced' with replaced_by; the stop stays 'held' until the new TP fills,
    then OCO cancels it.
  * qty can't be changed on advanced orders (422) -> partial fills and
    quantity mismatches go to the fallback, never to a PATCH.
  * a second replace on an already-replaced ID is 422 -> we always act on the
    LIVE order (re-read every step; never a cached ID).
  * after a replace the open-orders list no longer shows the held stop; it is
    in the symbol's full history -> every step reads that history.
  * a cancel sent outside market hours is processed later and takes the
    linked stop with it -> the manager only acts in market hours.

The live state is REBUILT FROM ALPACA on every step; local fields only hold
timers and counters. Every transition is journalled. Nothing here changes
entry rules, sizing, stops or limits: it only changes HOW a decided exit is
executed.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional

from ..trading.alpaca_client import OrderOutcomeUnknown, OrderRejected, WORKING_STATUSES
from ..trading.order_ids import attempts_at_broker, order_id, position_ref, prefix_for

logger = logging.getLogger(__name__)

EXIT_REQUESTED, REPRICING, EXIT_WORKING = "EXIT_REQUESTED", "REPRICING", "EXIT_WORKING"
FALLBACK_CANCEL, SELLING, RESTORE_PROTECTION = "FALLBACK_CANCEL", "SELLING", "RESTORE_PROTECTION"
UNCERTAIN, ESCALATED, CLOSED, RECONCILED = "UNCERTAIN", "ESCALATED", "CLOSED", "RECONCILED"

PENDING = {"pending_replace", "pending_cancel", "pending_new", "accepted"}
STOP_TYPES = {"stop", "stop_limit", "trailing_stop"}

REPRICE_CONFIRM_S = 10.0
PRICE_WAIT_S = 120.0
MAX_PRICES = 3
PARTIAL_WAIT_S = 300.0
CANCEL_CONFIRM_S = 20.0
SELL_WAIT_S = 60.0
MAX_FAILED_EXITS = 3
BID_DISCOUNT = 0.001          # marketable sell limit: 0.1% under the bid


@dataclass
class Exit:
    symbol: str
    reason: str
    requested_at: float
    state: str = EXIT_REQUESTED
    prices: int = 0                       # marketable prices tried
    priced_at: Optional[float] = None
    last_cid: Optional[str] = None        # newest R/X client ID we sent
    rejects: int = 0
    partial_since: Optional[float] = None
    cancel_at: Optional[float] = None
    sell_at: Optional[float] = None
    failed_exits: int = 0
    deferred: int = 0
    uncertain: Optional[dict] = None      # {cid, kind, at, sweeps}
    history: List[dict] = field(default_factory=list)


class ExitManager:
    def __init__(self, broker, alerts, env_tag: str, *, lock=None, protection=None,
                 lease_ok: Callable[[], bool] = lambda: True,
                 journal: Optional[Callable[[str, dict], None]] = None,
                 on_closed: Optional[Callable[[str, str, Optional[dict]], None]] = None,
                 clock: Callable[[], float] = time.time,
                 market_open: Optional[Callable[[], bool]] = None,
                 market_tz: str = "America/New_York"):
        from zoneinfo import ZoneInfo

        self.broker, self.alerts, self.env_tag = broker, alerts, env_tag
        self.lock = lock or threading.RLock()
        self.protection = protection
        self.lease_ok = lease_ok
        self.journal = journal or (lambda kind, payload: None)
        self.on_closed = on_closed or (lambda sym, reason, fill: None)
        self.clock = clock
        tz = ZoneInfo(market_tz)
        self.market_open = market_open or (lambda: _regular_hours(datetime.now(tz)))
        self.exits: Dict[str, Exit] = {}
        self._stop = threading.Event()

    # ---- public ------------------------------------------------------------------
    def request(self, symbol: str, reason: str) -> str:
        """Start (or continue) the exit for `symbol` and take one step now.
        Returns the state after that step. The reason is journalled only; it
        is never part of an order ID."""
        with self.lock:
            e = self.exits.get(symbol)
            if e is None:
                e = Exit(symbol, reason, self.clock())
                self.exits[symbol] = e
                self._note(e, EXIT_REQUESTED, f"exit requested: {reason}")
            self.step(symbol)
            return self.exits[symbol].state if symbol in self.exits else RECONCILED

    HOLD_OFF_STATES = (FALLBACK_CANCEL, SELLING, UNCERTAIN)
    HOLD_OFF_MAX_S = 300.0

    def holds_protection(self, symbol: str) -> bool:
        """True while this exit is between cancelling the legs and the sell
        filling, so the protection manager doesn't put a new protective order
        on the shares the sell needs. Never longer than 5 minutes after the
        last transition: a stuck exit must not leave a position unprotected."""
        e = self.exits.get(symbol)
        if e is None or e.state not in self.HOLD_OFF_STATES or not e.history:
            return False
        return self.clock() - e.history[-1]["at"] < self.HOLD_OFF_MAX_S

    def active(self) -> Dict[str, str]:
        return {s: e.state for s, e in self.exits.items()}

    def tick(self) -> None:
        if not self.exits or not self.lock.acquire(timeout=1.0):
            return
        try:
            for sym in list(self.exits):
                try:
                    self.step(sym)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Exit manager step failed for %s: %s", sym, exc)
        finally:
            self.lock.release()

    def start(self, tick_s: float = 5.0) -> None:
        def _loop():
            while not self._stop.wait(tick_s):
                self.tick()
        threading.Thread(target=_loop, daemon=True, name="exit-manager").start()

    def stop(self) -> None:
        self._stop.set()

    # ---- one step ----------------------------------------------------------------
    def step(self, sym: str) -> None:
        e = self.exits.get(sym)
        if e is None or e.state in (RECONCILED,):
            return
        now = self.clock()
        if not self.lease_ok():
            return self._defer(e, "trading lease not verified (D8: no submissions)")
        if not self.market_open():
            return self._defer(e, "market closed", count=False)
        v = self._read(sym)
        if v is None:
            return self._defer(e, "broker positions/orders unreadable (UNKNOWN)")
        e.deferred = 0

        if v["qty"] <= 0:
            return self._closed(e, v)
        if v["foreign"]:
            return self._escalate(e, f"foreign working order(s) on {sym}: "
                                     f"{[o.get('client_order_id') or o.get('id') for o in v['foreign']]} "
                                     "- hands off")
        if e.uncertain is not None:
            if not self._resolve_uncertain(e, v):
                return
            v = self._read(sym)
            if v is None:
                return self._defer(e, "broker unreadable after resolving")
        if v["pending"]:
            return   # replace/cancel in flight at Alpaca: wait, never stack a request

        if v["x"]:
            return self._selling(e, v, now)
        tp = v["tp"]
        if tp is not None:
            return self._with_tp(e, v, tp, now)
        if v["working"]:
            return self._fallback_cancel(e, v, now)
        # Nothing working on the symbol and shares still held.
        if e.state == FALLBACK_CANCEL:
            if v["avail"] + 1e-6 < v["qty"]:
                if now - (e.cancel_at or now) > CANCEL_CONFIRM_S:
                    return self._uncertain_cancel(e, "shares still held for orders after cancels")
                return
            return self._sell(e, v, now)
        if e.state in (SELLING, RESTORE_PROTECTION):
            return self._restore(e, "fallback sell did not complete")
        # Exit requested on a position with no protection at all: protect first,
        # the exit is re-decided at the next cycle (design: reconciliation table).
        return self._restore(e, "position had no working orders; protecting before exiting")

    # ---- the take-profit path ------------------------------------------------------
    def _with_tp(self, e: Exit, v: dict, tp: dict, now: float) -> None:
        filled = tp.get("filled_qty") or 0.0
        if filled > 0:
            if e.state != EXIT_WORKING or e.partial_since is None:
                e.partial_since = e.partial_since or now
                self._note(e, EXIT_WORKING, f"take-profit partially filled ({filled:g} of {tp.get('qty'):g}); "
                                            "never replacing a partially filled order")
            if now - e.partial_since >= PARTIAL_WAIT_S:
                return self._fallback_cancel(e, v, now, why="partial fill stalled 5 min")
            return
        if abs((tp.get("qty") or 0.0) - v["qty"]) > 1e-6:
            return self._fallback_cancel(e, v, now, why=f"take-profit qty {tp.get('qty'):g} != position "
                                                        f"{v['qty']:g} (qty can't be changed by replace)")
        cid_live = tp.get("client_order_id") or ""
        if e.last_cid is None and cid_live.startswith(f"{self.env_tag}R-{e.symbol}-"):
            # Rebuilt after a restart (or by a new instance): the live
            # take-profit is already one of our replacements. Adopt it rather
            # than sending another price straight away (6c test 12/13).
            e.last_cid, e.priced_at = cid_live, now
            e.prices = sum(1 for o in v["orders"]
                           if (o.get("client_order_id") or "").startswith(f"{self.env_tag}R-{e.symbol}-"))
            self._note(e, EXIT_WORKING, f"adopted live replacement {cid_live} after restart "
                                        f"({e.prices} price(s) already tried)")
            return
        mine = e.last_cid is not None and cid_live == e.last_cid
        if e.state == REPRICING and not mine:
            if e.priced_at is None:
                return self._reprice(e, v, tp, now)          # after one rejection
            if now - e.priced_at > REPRICE_CONFIRM_S:
                # Response said OK but the live order isn't ours: don't trust it.
                e.rejects += 1
                if e.rejects >= 2:
                    return self._fallback_cancel(e, v, now, why="replacement not confirmed twice")
                return self._reprice(e, v, tp, now)
            return
        if mine and e.state == REPRICING:
            self._note(e, EXIT_WORKING, f"replacement {e.last_cid} working at {tp.get('limit_price')}")
        if e.state == EXIT_WORKING and mine:
            if now - (e.priced_at or now) < PRICE_WAIT_S:
                return
            if e.prices >= MAX_PRICES:
                if e.state != ESCALATED:
                    self._escalate(e, f"{e.symbol}: marketable limit unfilled after {e.prices} prices; "
                                      "limit stays live, stop stays held (position still protected)")
                return
        if e.state == ESCALATED:
            return
        return self._reprice(e, v, tp, now)

    def _reprice(self, e: Exit, v: dict, tp: dict, now: float) -> None:
        bid = self.broker.latest_bid(e.symbol)
        if not bid:
            return self._defer(e, "no bid available for a marketable price")
        price = max(0.01, math.floor(bid * (1 - BID_DISCOUNT) * 100) / 100)
        cid = self._next_cid(e, v, "R")
        if cid is None:
            return
        self._note(e, REPRICING, f"replace take-profit {tp.get('id')} -> {price:.2f} as {cid}")
        e.last_cid, e.priced_at = cid, now
        try:
            self.broker.replace_order_once(tp["id"], price, cid, f"reprice {e.symbol}")
            e.prices += 1
        except OrderRejected as exc:
            e.rejects += 1
            self.journal("exit_reprice_rejected", {"symbol": e.symbol, "cid": cid, "error": str(exc)})
            if e.rejects >= 2:
                return self._fallback_cancel(e, self._read(e.symbol) or v, now,
                                             why=f"replace rejected twice: {exc}")
            e.priced_at = None   # one more try at the next step, fresh read and price
        except OrderOutcomeUnknown as exc:
            e.uncertain = {"cid": cid, "kind": "R", "at": now, "sweeps": 0}
            self._note(e, UNCERTAIN, f"replace outcome unknown: {exc}")

    # ---- fallback: cancel -> confirm -> sell ------------------------------------------
    def _fallback_cancel(self, e: Exit, v: dict, now: float, why: str = "no usable take-profit leg") -> None:
        if e.state != FALLBACK_CANCEL:
            self._note(e, FALLBACK_CANCEL, why)
            e.cancel_at = now
            for o in v["working"]:
                try:
                    self.broker.cancel_order_once(o["id"], f"fallback cancel {e.symbol}")
                except Exception as exc:  # noqa: BLE001
                    logger.error("Fallback cancel of %s failed: %s", o.get("id"), exc)
            return
        if v["working"] and now - (e.cancel_at or now) > CANCEL_CONFIRM_S:
            return self._uncertain_cancel(e, f"{len(v['working'])} order(s) still working 20 s after cancel")

    def _uncertain_cancel(self, e: Exit, why: str) -> None:
        # Never sell while a leg may still be live: that is how an oversell happens.
        if e.state != UNCERTAIN:
            self._note(e, UNCERTAIN, f"cancel not confirmed: {why}; NOT selling")
            e.uncertain = {"cid": None, "kind": "cancel", "at": self.clock(), "sweeps": 0}
        self.alerts.raise_(f"exit_uncertain:{e.symbol}",
                           f"{e.symbol}: exit paused - cancel not confirmed ({why}). No sell is sent "
                           "while a protective leg may still be live.", "critical", 3600)

    def _sell(self, e: Exit, v: dict, now: float) -> None:
        cid = self._next_cid(e, v, "X")
        if cid is None:
            return
        body = {"symbol": e.symbol, "qty": f"{v['avail']:g}", "side": "sell", "type": "market",
                "time_in_force": "day", "client_order_id": cid}
        self._note(e, SELLING, f"fallback market sell {v['avail']:g} sh as {cid}")
        e.last_cid, e.sell_at = cid, now
        try:
            self.broker.submit_order_once(body, "exit", f"fallback sell {e.symbol}")
        except OrderRejected as exc:
            return self._restore(e, f"fallback sell rejected: {exc}")
        except OrderOutcomeUnknown as exc:
            e.uncertain = {"cid": cid, "kind": "X", "at": now, "sweeps": 0}
            self._note(e, UNCERTAIN, f"sell outcome unknown: {exc}")

    def _selling(self, e: Exit, v: dict, now: float) -> None:
        x = v["x"][0]
        if e.state != SELLING:
            self._note(e, SELLING, f"exit sell {x.get('client_order_id')} working")
            e.sell_at = e.sell_at or now
        if (x.get("filled_qty") or 0) == 0 and now - (e.sell_at or now) > SELL_WAIT_S:
            self._note(e, RESTORE_PROTECTION, "fallback sell unfilled after 60 s; cancelling it")
            try:
                self.broker.cancel_order_once(x["id"], f"cancel unfilled exit {e.symbol}")
            except Exception as exc:  # noqa: BLE001
                logger.error("Cancel of unfilled exit %s failed: %s", x.get("id"), exc)

    # ---- protection hand-back, uncertainty, closing --------------------------------------
    def _restore(self, e: Exit, why: str) -> None:
        e.failed_exits += 1
        self._note(e, RESTORE_PROTECTION, why)
        if self.protection is not None:
            try:
                self.protection.sweep()     # same sweep: restores if confirmed missing
            except Exception as exc:  # noqa: BLE001
                logger.error("Protection sweep after failed exit (%s) failed: %s", e.symbol, exc)
        if e.failed_exits >= MAX_FAILED_EXITS:
            self.alerts.raise_(f"exit_failed:{e.symbol}",
                               f"{e.symbol}: exit failed {e.failed_exits} times ({why}). Position kept "
                               "under protection; check it in the Alpaca dashboard.", "critical", 3600)
        # Exit retried from the next step with the next attempt number.
        e.prices, e.rejects, e.priced_at, e.cancel_at, e.sell_at, e.partial_since = 0, 0, None, None, None, None
        e.state = EXIT_REQUESTED

    def _resolve_uncertain(self, e: Exit, v: dict) -> bool:
        u = e.uncertain
        u["sweeps"] += 1
        if u["kind"] == "cancel":
            if not v["working"]:
                e.uncertain = None
                self.alerts.resolve(f"exit_uncertain:{e.symbol}", f"{e.symbol}: cancels confirmed; exit resumes.")
                e.state = FALLBACK_CANCEL
                return True
            return False
        snap = self.broker.order_by_client_id(u["cid"])
        if not snap.ok:
            if u["sweeps"] >= 2:
                self.alerts.raise_(f"exit_uncertain:{e.symbol}",
                                   f"{e.symbol}: order {u['cid']} outcome still unknown after 2 checks. "
                                   "Nothing new is sent until it is settled.", "critical", 3600)
            return False
        e.uncertain = None
        self.alerts.resolve(f"exit_uncertain:{e.symbol}", f"{e.symbol}: order {u['cid']} settled.")
        if snap.data:
            self._note(e, EXIT_WORKING if u["kind"] == "R" else SELLING,
                       f"uncertain {u['cid']} found at Alpaca (status {snap.data.get('status')})")
            if u["kind"] == "R":
                e.prices += 1
        else:
            # Confirmed not found: the next attempt reuses the same number
            # (attempts are counted from Alpaca, where it doesn't exist).
            self._note(e, EXIT_REQUESTED if u["kind"] == "R" else FALLBACK_CANCEL,
                       f"uncertain {u['cid']} confirmed not at Alpaca")
        return True

    def _closed(self, e: Exit, v: dict) -> None:
        fill = next((o for o in v["orders"] if o.get("side") == "sell" and o.get("status") == "filled"),
                    None)
        self._note(e, CLOSED, f"position 0 confirmed (sell fill {fill.get('client_order_id') if fill else 'n/a'} "
                              f"@ {fill.get('filled_avg_price') if fill else 'n/a'})")
        try:
            self.on_closed(e.symbol, e.reason, fill)
        except Exception as exc:  # noqa: BLE001
            logger.error("Ledger write after exit of %s failed: %s", e.symbol, exc)
        for key in (f"exit_uncertain:{e.symbol}", f"exit_failed:{e.symbol}", f"exit_escalated:{e.symbol}"):
            self.alerts.resolve(key, f"{e.symbol}: position closed.")
        self._note(e, RECONCILED, "ledger written from the fill")
        self.exits.pop(e.symbol, None)

    def _escalate(self, e: Exit, why: str) -> None:
        if e.state != ESCALATED:
            self._note(e, ESCALATED, why)
        self.alerts.raise_(f"exit_escalated:{e.symbol}", f"EXIT NEEDS ATTENTION: {why}", "critical", 3600)

    def _defer(self, e: Exit, why: str, count: bool = True) -> None:
        if count:
            e.deferred += 1
            if e.deferred == 2:
                self.alerts.raise_(f"exit_deferred:{e.symbol}",
                                   f"{e.symbol}: exit ({e.reason}) deferred twice: {why}.", "critical", 3600)
        logger.info("Exit %s deferred: %s", e.symbol, why)

    # ---- broker view (rebuilt every step) ----------------------------------------------
    def _read(self, sym: str) -> Optional[dict]:
        pos = self.broker.positions_snapshot()
        hist = self.broker.symbol_orders_snapshot(sym)
        if not pos.ok or not hist.ok:
            return None
        p = (pos.data or {}).get(sym) or {}
        orders = [o for o in (hist.data or []) if o.get("symbol") == sym]
        working = [o for o in orders if o.get("side") == "sell" and o.get("status") in WORKING_STATUSES]
        x_prefix = f"{self.env_tag}X-{sym}-"
        x = [o for o in working if (o.get("client_order_id") or "").startswith(x_prefix)]
        legs = [o for o in working if o.get("group_role") in ("take_profit", "stop_loss")
                or o.get("order_class") in ("bracket", "oco")]
        tp = next((o for o in legs if o.get("group_role") == "take_profit"
                   or (o.get("type") == "limit" and o.get("group_id"))), None)
        ours = (f"{self.env_tag}P-", f"{self.env_tag}R-", f"{self.env_tag}X-")
        foreign = [o for o in working if o not in legs and o not in x
                   and not (o.get("client_order_id") or "").startswith(ours)]
        return {"qty": float(p.get("qty") or 0.0), "avail": float(p.get("qty_available") or 0.0),
                "position": p, "orders": orders, "working": working, "x": x, "tp": tp,
                "foreign": foreign, "pending": [o for o in working if o.get("status") in PENDING]}

    def _next_cid(self, e: Exit, v: dict, kind: str) -> Optional[str]:
        entry = next((o for o in v["orders"] if o.get("side") == "buy" and o.get("status") == "filled"),
                     None)
        ref = position_ref(entry.get("id") if entry else None, v["position"])
        prefix = prefix_for(self.env_tag, kind, e.symbol, ref)
        used, _ = attempts_at_broker(v["orders"], prefix)
        return order_id(self.env_tag, kind, e.symbol, ref, used + 1)

    def _note(self, e: Exit, state: str, why: str) -> None:
        old, e.state = e.state, state
        row = {"at": self.clock(), "from": old, "to": state, "why": why}
        e.history.append(row)
        logger.warning("Exit %s: %s -> %s (%s)", e.symbol, old, state, why)
        try:
            self.journal("exit_transition", {"symbol": e.symbol, "reason": e.reason, **row})
        except Exception as exc:  # noqa: BLE001
            logger.error("Exit journal write failed: %s", exc)
        faults = getattr(self, "faults", None)
        if faults is not None:
            faults.on_transition(state)     # 6c kill points (staging only)


def _regular_hours(t: datetime) -> bool:
    if t.weekday() >= 5:
        return False
    m = t.hour * 60 + t.minute
    return 9 * 60 + 30 <= m < 16 * 60
