"""Staging-only TEST entries (review 11 Nov, item 5).

Purpose: exercise the exit system while the SPY regime filter blocks the
normal strategy's entries. It replaces ONLY the strategy's entry decision
and regime filter. Every other control still applies: the trading lease,
the order guard, single-attempt submission, the protection manager and its
entry gate, and reconciliation.

Operator-triggered, never self-scheduling:
    STAGING_TEST_ENTRIES_ENABLED=true
    STAGING_TEST_ENTRY_REQUEST=<request id>:<SYMBOL>:<minutes to hold>
        e.g.  t01:VZ:20  -> one share of VZ; exit requested 20 min after the fill
A request ID is used once, ever (state on the staging volume). A new test
needs a new ID. Kill switch: STAGING_TEST_ENTRIES_KILL=true or the file
<state dir>/KILL - no new entry from that moment; an open test position is
left to the normal exit and protection logic.

Safeguards
  * the runner refuses to exist unless BOT_ENVIRONMENT=staging AND the
    deployment guard verified the expected Alpaca paper account;
  * whitelist of liquid, out-of-universe symbols; 1 share; at most ONE test
    position at a time; a fixed dollar cap per entry ($100);
  * no new entry until the previous test position is flat AND every order on
    the test symbols is final (fully reconciled);
  * the entry's protection must be confirmed (protection manager PROTECTED)
    before the hold timer starts;
  * a hard maximum number of test entries per deployment (10);
  * every order ID starts sTEST-, every journal event is kind test_*, every
    trade row is entry_path TEST.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Callable, Optional

from ..trading.alpaca_client import WORKING_STATUSES, OrderOutcomeUnknown, OrderRejected

logger = logging.getLogger(__name__)

TEST_SYMBOLS = ("VZ", "KMI", "CSX")   # liquid, under $100, outside the universe (checked at start)
MAX_NOTIONAL = 100.0
MAX_ENTRIES_PER_DEPLOYMENT = 10
QTY = 1


class TestEntryRefused(RuntimeError):
    __test__ = False      # not a pytest test class


class TestEntryRunner:
    __test__ = False      # not a pytest test class
    def __init__(self, broker, strategy, *, env: dict, state_dir: str, account_verified: bool,
                 universe, protection, lease_ok: Callable[[], bool], journal=None,
                 market_open: Optional[Callable[[], bool]] = None, clock: Callable[[], float] = time.time):
        if (env.get("BOT_ENVIRONMENT") or "").lower() != "staging":
            raise TestEntryRefused("test entries exist only when BOT_ENVIRONMENT=staging")
        if not account_verified:
            raise TestEntryRefused("test entries need the staging Alpaca paper account verified")
        overlap = set(TEST_SYMBOLS) & set(universe)
        if overlap:
            raise TestEntryRefused(f"test symbols {sorted(overlap)} are in the trading universe")
        if protection is None:
            raise TestEntryRefused("test entries need the protection manager (PROTECTION_SWEEP_ENABLED)")
        self.broker, self.strategy, self.env = broker, strategy, env
        self.protection, self.lease_ok = protection, lease_ok
        self.journal = journal or (lambda kind, payload: None)
        self.market_open = market_open or (lambda: bool(getattr(broker, "is_market_open", lambda: False)()))
        self.clock = clock
        self.state_dir = state_dir
        os.makedirs(state_dir, exist_ok=True)
        self.state_path = os.path.join(state_dir, "test_entries.json")
        self.entries_this_deployment = 0
        self.state = self._load()

    # ---- durable state ---------------------------------------------------------
    def _load(self) -> dict:
        try:
            with open(self.state_path) as fh:
                return json.load(fh)
        except FileNotFoundError:
            return {"used": [], "active": None}

    def _save(self) -> None:
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.state, fh)
        os.replace(tmp, self.state_path)

    def _event(self, kind: str, **payload) -> None:
        payload = {"test": True, **payload}
        logger.warning("TEST %s %s", kind, payload)
        try:
            self.journal(kind, payload)
        except Exception as exc:  # noqa: BLE001
            logger.error("test-entry journal write failed: %s", exc)

    # ---- controls --------------------------------------------------------------
    def killed(self) -> bool:
        return (self.env.get("STAGING_TEST_ENTRIES_KILL", "").lower() in ("1", "true", "yes")
                or os.path.exists(os.path.join(self.state_dir, "KILL")))

    def parse_request(self) -> Optional[dict]:
        raw = (self.env.get("STAGING_TEST_ENTRY_REQUEST") or "").strip()
        if not raw:
            return None
        parts = raw.split(":")
        if len(parts) != 3:
            raise TestEntryRefused(f"bad STAGING_TEST_ENTRY_REQUEST {raw!r} (want id:SYMBOL:minutes)")
        rid, sym, mins = parts[0].strip(), parts[1].strip().upper(), float(parts[2])
        if sym not in TEST_SYMBOLS:
            raise TestEntryRefused(f"{sym} is not a test symbol {TEST_SYMBOLS}")
        if not 1 <= mins <= 240:
            raise TestEntryRefused("hold minutes must be 1-240")
        return {"id": rid, "symbol": sym, "hold_min": mins}

    # ---- the one call per cycle --------------------------------------------------
    def tick(self) -> str:
        """Advance the active test (if any), else consider the pending request.
        Returns a short status for logs/tests."""
        active = self.state.get("active")
        if active:
            return self._advance(active)
        if self.killed():
            return "killed"
        try:
            req = self.parse_request()
        except TestEntryRefused as exc:
            self._event("test_refused", reason=str(exc))
            return "refused"
        if req is None:
            return "no request"
        if req["id"] in self.state["used"]:
            return "request already used"
        why = self._entry_blocked(req)
        if why:
            logger.info("TEST entry %s waiting: %s", req["id"], why)
            return f"waiting: {why}"
        return self._enter(req)

    def _test_positions_and_orders(self):
        pos = self.broker.positions_snapshot()
        if not pos.ok:
            return None, None
        held = {s: p for s, p in (pos.data or {}).items() if s in TEST_SYMBOLS}
        working = []
        for sym in TEST_SYMBOLS:
            h = self.broker.symbol_orders_snapshot(sym)
            if not h.ok:
                return None, None
            working += [o for o in h.data or [] if o.get("status") in WORKING_STATUSES]
        return held, working

    def _entry_blocked(self, req) -> Optional[str]:
        if self.entries_this_deployment >= MAX_ENTRIES_PER_DEPLOYMENT:
            return f"hard cap of {MAX_ENTRIES_PER_DEPLOYMENT} test entries per deployment reached"
        if not self.lease_ok():
            return "trading lease not verified"
        if not self.market_open():
            return "market closed"
        blocked, why = self.protection.entry_blocker()
        if blocked:
            return f"protection gate: {why}"
        held, working = self._test_positions_and_orders()
        if held is None:
            return "broker state unknown"
        if held or working:
            return "previous test position/orders not fully reconciled"
        quote = self.broker.latest_quote(req["symbol"]) if hasattr(self.broker, "latest_quote") else None
        ask = (quote or {}).get("ask") or 0
        if not ask or ask * QTY > MAX_NOTIONAL:
            return f"price check failed (ask {ask}, cap ${MAX_NOTIONAL:.0f})"
        req["ask"] = ask
        return None

    def _enter(self, req) -> str:
        env_tag = getattr(self.broker, "env_tag", "s")
        cid = f"{env_tag}TEST-{req['symbol']}-{req['id']}"
        stop = round(req["ask"] * (1 - self.strategy.cfg.risk.stop_loss_pct / 100.0), 2)
        tp = round(req["ask"] * (1 + self.strategy.cfg.risk.take_profit_pct / 100.0), 2)
        # Used BEFORE sending: a request is never retried by this runner.
        self.state["used"].append(req["id"])
        self.state["active"] = {**req, "cid": cid, "phase": "entering", "at": self.clock()}
        self._save()
        self.entries_this_deployment += 1
        self._event("test_entry_submit", request=req["id"], symbol=req["symbol"], cid=cid, stop=stop, tp=tp)
        try:
            order = self.broker.submit_bracket_buy(req["symbol"], QTY, stop, tp, client_order_id=cid)
        except (OrderRejected, OrderOutcomeUnknown) as exc:
            self._event("test_entry_failed", request=req["id"], error=str(exc))
            self.state["active"]["phase"] = "unsure" if isinstance(exc, OrderOutcomeUnknown) else "done"
            if isinstance(exc, OrderRejected):
                self.state["active"] = None
            self._save()
            return "entry failed"
        self.strategy.recorder.record_trade(
            action="buy", symbol=req["symbol"], qty=QTY, price=req["ask"], notional=req["ask"] * QTY,
            sentiment=None, stop_price=stop, take_profit=tp, reason=f"TEST entry {req['id']}",
            rationale="staging test entry (exit-system validation)", dry_run=False,
            order_id=order.order_id, status=order.status, sector="TEST", entry_path="TEST")
        self.state["active"]["phase"] = "awaiting_protection"
        self._save()
        return "entered"

    def _advance(self, a: dict) -> str:
        sym = a["symbol"]
        held, working = self._test_positions_and_orders()
        if held is None:
            return "broker state unknown"
        qty = float((held.get(sym) or {}).get("qty") or 0)
        if a["phase"] in ("entering", "unsure", "awaiting_protection"):
            if qty <= 0:
                if self.clock() - a["at"] > 600:
                    self._event("test_entry_never_filled", request=a["id"])
                    self.state["active"] = None
                    self._save()
                    return "never filled"
                return "waiting for fill"
            track = self.protection.tracks.get(sym)
            if track is None or track.state != "PROTECTED":
                return "waiting for confirmed protection"
            a.update(phase="holding", protected_at=self.clock())
            self._save()
            self._event("test_entry_protected", request=a["id"], symbol=sym)
            return "protected"
        if a["phase"] == "holding":
            if qty <= 0:
                return self._finish(a, "closed by broker (stop or take-profit)")
            if self.clock() - a["protected_at"] < a["hold_min"] * 60:
                return "holding"
            a["phase"] = "exiting"
            self._save()
            self._event("test_exit_request", request=a["id"], symbol=sym)
            from types import SimpleNamespace
            sent = SimpleNamespace(score=0.0, label="test", article_count=0, rationale="TEST exit",
                                   positive_count=0, negative_count=0)
            self.strategy._do_sell(sym, sent, f"TEST exit {a['id']}", entry_path="TEST")
            return "exit requested"
        if a["phase"] == "exiting":
            if qty <= 0 and not working:
                return self._finish(a, "closed by the exit")
            xm = getattr(self.strategy, "exit_manager", None)
            if xm is None or sym not in xm.exits:
                from types import SimpleNamespace
                sent = SimpleNamespace(score=0.0, label="test", article_count=0, rationale="TEST exit",
                                       positive_count=0, negative_count=0)
                self.strategy._do_sell(sym, sent, f"TEST exit {a['id']}", entry_path="TEST")
            return "exiting"
        return a["phase"]

    def _finish(self, a: dict, how: str) -> str:
        self._event("test_closed", request=a["id"], symbol=a["symbol"], how=how)
        self.state["active"] = None
        self._save()
        return "closed"
