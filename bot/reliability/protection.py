"""Protection status and restoration (Reliability plan v3, 2H).

Every held position is classified from a FRESH broker read:

  PROTECTED        a working sell stop (bracket/OCO stop leg, or a stand-alone
                   stop) covers at least the full position quantity
  UNDER_PROTECTED  a stop exists but covers fewer shares than are held
  UNPROTECTED      positions AND orders were read successfully and no stop
                   covers the position
  UNKNOWN          the positions or orders read failed - NOT "unprotected"

Behaviour (approved plan, "Protection model"):
  * Sweep every 5 minutes in market hours (30 minutes outside), plus at the
    start of every trading cycle, plus follow-ups while a restore is pending.
  * UNPROTECTED / UNDER_PROTECTED: restore at once with a protective OCO
    (stop at the existing -10%-from-entry rule, take-profit at the existing
    backstop rule; Alpaca's average entry price) for the uncovered quantity.
    Attempts at 0, 60 and 120 s after detection; each must be confirmed by a
    later sweep. After 3 failed attempts: critical "manual action needed",
    repeated every 15 minutes, and a /fail ping to the external monitor.
  * UNKNOWN: no protective order is placed (it could duplicate one we can't
    see). Re-read on every sweep. Alerts: if the position was confirmed
    protected within the last 30 minutes, a warning after 10 minutes and a
    critical alert after 30; otherwise critical at once. Repeats every 15 min.
  * New entries are blocked account-wide while any position is UNKNOWN, any
    position has been unprotected for more than 5 minutes, or the protection
    status itself is stale (no successful sweep for 15 minutes).
  * If the price is already below the stop level, no stop is placed (it
    would trigger at once); critical alert instead. Automatic exit in that
    case is decision D13, part of the Stage 6 exit manager.

Order identity: `<env>P-<SYMBOL>-<position ref>-<attempt>`, the attempt
number counted from orders already at Alpaca, so a restart or a second
instance computes the same ID. An uncertain submission (timeout, 5xx) is
resolved by looking the ID up; the same ID is resubmitted only after Alpaca
confirms "not found" at least 60 s later with no other evidence of the order.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from ..trading.alpaca_client import (WORKING_STATUSES, OrderBlockedError, classify_submit_error)
from ..trading.order_ids import attempts_at_broker, order_id, position_ref  # noqa: F401 - re-exported

logger = logging.getLogger(__name__)

PROTECTED, UNDER_PROTECTED, UNPROTECTED, UNKNOWN = "PROTECTED", "UNDER_PROTECTED", "UNPROTECTED", "UNKNOWN"
# Working stops cover MORE shares than are held (e.g. a take-profit partly
# filled and Alpaca did not shrink the linked stop). Never assumed fine: a
# stop for more than is held could sell more than is held. Alert + block
# entries; no automatic order until Alpaca's behaviour is verified (6c V5).
OVER_PROTECTED = "OVER_PROTECTED"
STOP_TYPES = {"stop", "stop_limit", "trailing_stop"}
ATTEMPT_OFFSETS_S = (0.0, 60.0, 120.0)
CONFIRM_AFTER_S = 30.0
EPS = 1e-6


# ---- pure classification ---------------------------------------------------
def stop_coverage(orders: List[dict], symbol: str) -> float:
    """Shares covered by WORKING stop orders (stop legs of brackets/OCOs and
    stand-alone stops), remaining quantity only. Replaced/cancelled/filled/
    rejected orders don't count, and an order being replaced is counted once
    (its replacement, when both show as working)."""
    rows = [o for o in orders if o.get("symbol") == symbol and o.get("side") == "sell"]
    replaced_by_working = {o.get("replaces") for o in rows
                           if o.get("replaces") and o.get("status") in WORKING_STATUSES}
    covered, seen = 0.0, set()
    for o in rows:
        if o.get("status") not in WORKING_STATUSES or o.get("id") in seen:
            continue
        seen.add(o.get("id"))
        if o.get("id") in replaced_by_working:
            continue
        if o.get("group_role") == "stop_loss" or o.get("type") in STOP_TYPES:
            covered += max(0.0, (o.get("qty") or 0.0) - (o.get("filled_qty") or 0.0))
    return covered


def classify(positions: Optional[Dict[str, dict]], orders: Optional[List[dict]]) -> Dict[str, Tuple[str, float]]:
    """{symbol: (state, shares covered)}. positions=None -> caller treats as a
    global UNKNOWN; orders=None -> every held position is UNKNOWN."""
    out: Dict[str, Tuple[str, float]] = {}
    for sym, p in (positions or {}).items():
        qty = p.get("qty") or 0.0
        if orders is None or p.get("side") == "short" or qty < 0:
            out[sym] = (UNKNOWN, 0.0)
            continue
        cov = stop_coverage(orders, sym)
        if cov > qty + EPS:
            out[sym] = (OVER_PROTECTED, cov)
        elif cov + EPS >= qty:
            out[sym] = (PROTECTED, cov)
        elif cov > EPS:
            out[sym] = (UNDER_PROTECTED, cov)
        else:
            out[sym] = (UNPROTECTED, 0.0)
    return out


# ---- per-symbol tracking -------------------------------------------------------
@dataclass
class Track:
    state: str = UNKNOWN
    covered: float = 0.0
    missing_since: Optional[float] = None
    unknown_since: Optional[float] = None
    last_protected_at: Optional[float] = None
    attempts: List[dict] = field(default_factory=list)   # {at, cid, outcome}
    confirm_due: Optional[float] = None
    uncertain: Optional[dict] = None                      # {cid, at, qty, stop, tp}
    escalated: bool = False
    emergency: Optional[dict] = None                      # {cid, at} once an emergency exit was sent
    ptp: Optional[dict] = None                            # partial take-profit recovery {tp, at, tries}


class ProtectionManager:
    def __init__(self, broker, risk_cfg, env_tag: str, alerts, *, lock=None, heartbeat=None,
                 dry_run: bool = False, on_protected: Optional[Callable[[str], None]] = None,
                 clock: Callable[[], float] = time.time, sweep_s: float = 300.0,
                 off_hours_sweep_s: float = 1800.0, market_tz: str = "America/New_York",
                 now_local: Optional[Callable[[], datetime]] = None):
        from zoneinfo import ZoneInfo

        self.broker, self.risk, self.env_tag, self.alerts = broker, risk_cfg, env_tag, alerts
        self.lock = lock or threading.RLock()
        self.heartbeat = heartbeat
        self.dry_run = dry_run
        self.on_protected = on_protected or (lambda s: None)
        self.clock = clock
        self.sweep_s, self.off_hours_sweep_s = sweep_s, off_hours_sweep_s
        self.tz = ZoneInfo(market_tz)
        self.now_local = now_local or (lambda: datetime.now(self.tz))
        self.tracks: Dict[str, Track] = {}
        self.global_unknown_since: Optional[float] = None
        self.last_ok_sweep: Optional[float] = None
        self.next_sweep_at = 0.0
        # Emergency exit for a confirmed position already below its stop level
        # (review 7 Nov). Part of the Stage 6 exit manager: follows
        # EXIT_MANAGER_ENABLED, off by default -> escalate only.
        self.emergency_exit_enabled = False
        # Only the instance allowed to trade sweeps in the background (set to
        # the lease check by main.py); a standby instance stays silent.
        self.active: Callable[[], bool] = lambda: True
        self._stop = threading.Event()

    # ---- entry gate --------------------------------------------------------------
    def entry_blocker(self) -> Tuple[bool, str]:
        now = self.clock()
        if self.last_ok_sweep is None:
            return True, "protection status not yet verified"
        if self.global_unknown_since is not None:
            return True, "broker positions/orders unreadable: protection status UNKNOWN"
        if now - self.last_ok_sweep > 900:
            return True, "protection status stale (no successful sweep for 15 min)"
        for sym, t in self.tracks.items():
            if t.state == UNKNOWN:
                return True, f"{sym} protection status UNKNOWN"
            if t.state == OVER_PROTECTED:
                return True, f"{sym} stop covers more shares than are held"
            if t.state in (UNPROTECTED, UNDER_PROTECTED) and t.missing_since is not None \
                    and now - t.missing_since > 300:
                return True, f"{sym} unprotected for {int(now - t.missing_since)}s"
        return False, ""

    # ---- the sweep -----------------------------------------------------------------
    def sweep(self) -> Dict[str, str]:
        now = self.clock()
        pos = self.broker.positions_snapshot()
        orders = self.broker.open_orders_snapshot()
        if not pos.ok:
            self._global_unknown(now, f"positions unreadable: {pos.error}")
            return {}
        if self.global_unknown_since is not None:
            self.global_unknown_since = None
            self.alerts.resolve("prot_global", "Broker positions/orders readable again; protection "
                                               "status verified.")
        self.last_ok_sweep = now if orders.ok else self.last_ok_sweep
        states = classify(pos.data, orders.data if orders.ok else None)
        all_orders = list(orders.data or []) if orders.ok else None
        for sym, (state, _) in list(states.items()):
            if state not in (UNPROTECTED, UNDER_PROTECTED) or all_orders is None:
                continue
            states[sym], extra = self._confirm_missing(sym, pos.data, all_orders)
            all_orders.extend(extra)
        for gone in set(self.tracks) - set(states):
            self._clear(gone, "position closed")
        for sym, (state, covered) in states.items():
            t = self.tracks.setdefault(sym, Track())
            t.covered = covered
            if state == PROTECTED:
                self._protected(sym, t, now)
            elif state == OVER_PROTECTED:
                t.state, t.unknown_since = OVER_PROTECTED, None
                t.missing_since = t.missing_since or now
                self.alerts.raise_(
                    f"prot_over:{sym}",
                    f"{sym}: working stop orders cover {covered:g} shares but only "
                    f"{pos.data[sym].get('qty'):g} are held (e.g. after a partial take-profit fill). "
                    "A stop for more than is held could sell more than is held. New entries are "
                    "blocked; nothing is changed automatically - check it in the Alpaca dashboard.",
                    "critical", 3600)
            elif state == UNKNOWN:
                self._unknown(sym, t, now, orders.error or "position state anomalous")
            else:
                t.state = state
                t.unknown_since = None
                self._missing(sym, t, now, pos.data[sym], all_orders)
        if not orders.ok:
            self.global_unknown_since = self.global_unknown_since or now
        if orders.ok:
            try:
                self.orphan_sweep(now, pos.data or {})
            except Exception as exc:  # noqa: BLE001
                logger.error("Orphan-order sweep failed: %s", exc)
        return {s: t.state for s, t in self.tracks.items()}

    def _confirm_missing(self, sym: str, positions: Dict[str, dict], seen: List[dict]):
        """Second look before declaring a position under- or unprotected.

        Alpaca's open-orders list does not show a bracket's stop leg once the
        take-profit leg has been replaced: the replacement is listed on its
        own (legs=[]) and the still-'held' stop sits under the filled entry
        order, which is not 'open' (staging, 10 Nov, real order data). So the
        symbol's full order history is read and any working sell orders not
        already seen are added. If that read fails the position is UNKNOWN:
        no protective order is placed on a guess."""
        hist = self.broker.symbol_orders_snapshot(sym)
        if not hist.ok:
            logger.warning("Protection: second read for %s failed (%s); state UNKNOWN", sym, hist.error)
            return (UNKNOWN, 0.0), []
        ids = {o.get("id") for o in seen}
        extra = [o for o in hist.data or [] if o.get("symbol") == sym and o.get("id") not in ids
                 and o.get("status") in WORKING_STATUSES]
        if extra:
            logger.info("Protection: %d working order(s) for %s found only in its order history "
                        "(e.g. stop leg of a bracket whose take-profit was replaced)", len(extra), sym)
        return classify({sym: positions[sym]}, list(seen) + extra)[sym], extra

    # ---- orphan protective orders (review 12 Nov item 1) -------------------------------
    ORPHAN_GRACE_S = 60.0          # Alpaca's own OCO cancel can lag the fill (V4, 11 Nov)
    ORPHAN_LOOKBACK_H = 48

    def _ours(self, o: dict, sym: str) -> bool:
        import re

        cid = o.get("client_order_id") or ""
        env = self.env_tag
        return (cid.startswith((f"{env}P-", f"{env}R-", f"{env}X-", f"{env}TEST-", "adopt-", "spike-", "drill-"))
                or re.fullmatch(rf"{re.escape(sym)}-\d{{10}}(00|30)", cid) is not None)

    def find_orphans(self, sym: str, history: List[dict]) -> List[dict]:
        """Working SELL protective orders of a bot-managed position that is
        now closed. Positively identified only: the order is one of ours by
        client ID, or a stop/take-profit leg of a bracket/OCO group that
        contains one of our orders. Anything else (a manual order, an order
        with no group, a buy) is never touched."""
        rows = [o for o in history if o.get("symbol") == sym]
        our_groups = {o.get("group_id") for o in rows if o.get("group_id") and self._ours(o, sym)}
        out = []
        for o in rows:
            if o.get("side") != "sell" or o.get("status") not in WORKING_STATUSES:
                continue
            leg = o.get("group_role") in ("stop_loss", "take_profit") and o.get("group_id") in our_groups
            if self._ours(o, sym) or leg:
                out.append(o)
        return out

    def orphan_sweep(self, now: float, positions: Dict[str, dict]) -> List[str]:
        """Cancel working protective orders left behind by a fully closed
        managed position (broker-side fill, manual close, restart, an OCO
        sibling Alpaca hasn't cancelled). Needs fresh, successful reads; a
        candidate must still be orphaned after ORPHAN_GRACE_S; after the
        cancel, positions and the order history are re-read to confirm.
        Returns the IDs cancelled and confirmed."""
        recent = getattr(self.broker, "recent_order_symbols", None)
        if recent is None:
            return []
        syms = recent(self.ORPHAN_LOOKBACK_H)
        if syms is None:                         # unreadable: cancel nothing
            return []
        seen = getattr(self, "_orphan_seen", None)
        if seen is None:
            seen = self._orphan_seen = {}
        live_ids, done = set(), []
        for sym in sorted(set(syms) - set(positions)):
            hist = self.broker.symbol_orders_snapshot(sym)
            if not hist.ok:
                continue                          # UNKNOWN for this symbol: nothing cancelled
            for o in self.find_orphans(sym, hist.data or []):
                live_ids.add(o["id"])
                first = seen.setdefault(o["id"], now)
                if now - first < self.ORPHAN_GRACE_S:
                    continue
                done += self._cancel_orphan(sym, o, now)
        for oid in list(seen):
            if oid not in live_ids:
                seen.pop(oid, None)               # gone by itself (e.g. Alpaca's OCO cancel)
        return done

    def _cancel_orphan(self, sym: str, o: dict, now: float) -> List[str]:
        pos = self.broker.positions_snapshot()   # fresh re-read right before acting
        if not pos.ok or sym in (pos.data or {}):
            return []
        what = f"orphan {o.get('group_role') or o.get('type')} {o.get('client_order_id')} on closed {sym}"
        try:
            accepted = self.broker.cancel_order_once(o["id"], f"cancel {what}")
        except Exception as exc:  # noqa: BLE001
            accepted, err = None, str(exc)
        else:
            err = "" if accepted else "already final"
        after_pos, after = self.broker.positions_snapshot(), self.broker.symbol_orders_snapshot(sym)
        still = (not after.ok) or any(x.get("id") == o["id"] and x.get("status") in WORKING_STATUSES
                                      for x in after.data or [])
        if still or not after_pos.ok:
            self.alerts.raise_(f"orphan_failed:{sym}",
                               f"Could not confirm cancellation of {what} ({err or 'still working'}). "
                               "Check it in the Alpaca dashboard.", "critical", 3600)
            return []
        logger.warning("Cancelled %s (confirmed: no position, order no longer working)", what)
        self.alerts.resolve(f"orphan_failed:{sym}", f"{sym}: orphan order cleanup confirmed.")
        self._orphan_seen.pop(o["id"], None)
        return [o["id"]]

    def _global_unknown(self, now: float, why: str) -> None:
        self.global_unknown_since = self.global_unknown_since or now
        mins = (now - self.global_unknown_since) / 60
        self.alerts.raise_("prot_global",
                           f"PROTECTION STATUS UNKNOWN: {why}. No protective orders will be placed "
                           f"until the broker can be read (for {mins:.0f} min). Broker-side stops "
                           "already in place are unaffected. New entries are blocked.",
                           "critical", 900)
        if mins >= 30 and self.heartbeat is not None:
            self.heartbeat.fail(f"protection status unknown for {mins:.0f} min")

    def _protected(self, sym: str, t: Track, now: float) -> None:
        was = t.state
        t.state, t.last_protected_at = PROTECTED, now
        t.missing_since = t.unknown_since = t.confirm_due = None
        t.uncertain, t.attempts, t.escalated = None, [], False
        if was in (UNPROTECTED, UNDER_PROTECTED, UNKNOWN):
            for key in (f"prot_missing:{sym}", f"prot_failed:{sym}", f"prot_manual:{sym}",
                        f"prot_unknown:{sym}"):
                self.alerts.resolve(key, None)
            if was != UNKNOWN:
                self.alerts.raise_(f"prot_ok:{sym}", f"{sym}: protection confirmed at the broker.",
                                   "warning", 0)

    def _clear(self, sym: str, why: str) -> None:
        t = self.tracks.pop(sym, None)
        if t is not None and t.emergency is not None:
            self.alerts.resolve(f"prot_emergency:{sym}", f"{sym}: emergency exit confirmed - position "
                                                         "no longer held at the broker.", "critical")
        for key in (f"prot_missing:{sym}", f"prot_failed:{sym}", f"prot_manual:{sym}",
                    f"prot_unknown:{sym}"):
            self.alerts.resolve(key, None)

    def _unknown(self, sym: str, t: Track, now: float, why: str) -> None:
        t.state = UNKNOWN
        t.unknown_since = t.unknown_since or now
        dur = now - t.unknown_since
        recently_ok = t.last_protected_at is not None and now - t.last_protected_at <= 1800
        if recently_ok and dur < 600:
            return
        severity = "warning" if (recently_ok and dur < 1800) else "critical"
        self.alerts.raise_(f"prot_unknown:{sym}",
                           f"{sym}: protection status UNKNOWN for {dur / 60:.0f} min ({why}). Not "
                           "placing orders that could duplicate one we can't see. New entries "
                           "blocked. Check the Alpaca dashboard if this persists.", severity, 900)
        if dur >= 1800 and self.heartbeat is not None:
            self.heartbeat.fail(f"{sym} protection unknown for {dur / 60:.0f} min")

    # ---- restoration -----------------------------------------------------------------
    def _missing(self, sym: str, t: Track, now: float, pos: dict, orders: List[dict]) -> None:
        hold = getattr(self, "hold_off", None)
        if hold is not None and hold(sym, pos, orders):
            # Stage 6b: the exit manager is between "legs cancelled" and "sell
            # filled" for this symbol (bounded to 5 min by the exit manager).
            # A protective order now would hold the shares the sell needs.
            logger.info("Protection for %s held off: exit sell in progress", sym)
            return
        owns = getattr(self, "exit_owns", None)
        if owns is not None and owns(sym):
            # Review 13 Nov: one owner per recovery. While the exit manager is
            # closing this position it owns the remaining exit (bounded
            # fallback); the protection manager sends nothing competing.
            logger.info("Protection for %s: exit manager owns this position's recovery", sym)
            return
        if self._partial_tp_recovery(sym, t, now, pos, orders):
            return
        if t.missing_since is None:
            t.missing_since = now
            # Confirmed (after the order-history second look) with no stop at
            # all = a critical incident (VZ, 10 Nov); partial cover = warning.
            self.alerts.raise_(f"prot_missing:{sym}",
                               f"{sym} is {t.state.replace('_', ' ').lower()} at the broker "
                               f"({t.covered:g} of {pos.get('qty'):g} shares covered by a stop). "
                               "Restoring protection now.",
                               "critical" if t.state == UNPROTECTED else "warning", 900)
        if t.escalated:
            self._escalate(sym, t, now, None)
            return
        if t.uncertain is not None:
            self._resolve_uncertain(sym, t, now, pos, orders)
            return
        if t.confirm_due is not None:
            if now < t.confirm_due:
                return
            # The previous order was accepted but the position is still not
            # covered: that attempt failed.
            t.confirm_due = None
            if t.attempts:
                t.attempts[-1]["outcome"] = "not confirmed"
            self._attempt_failed(sym, t, "submitted order did not show up as a working stop")
        n_done = len(t.attempts)
        if n_done >= len(ATTEMPT_OFFSETS_S):
            self._escalate(sym, t, now, "3 restore attempts failed")
            return
        if now < t.missing_since + ATTEMPT_OFFSETS_S[n_done]:
            return
        self._restore(sym, t, now, pos, orders)

    # ---- partial take-profit recovery (review 13 Nov item 1) ----------------------------
    PTP_RETRY_CANCEL_S = 20.0
    PTP_ESCALATE_S = 90.0

    def _partial_tp_recovery(self, sym: str, t: Track, now: float, pos: dict,
                             orders: Optional[List[dict]]) -> bool:
        """A NORMAL take-profit partly filled and the remaining shares are not
        covered by a stop (staging 12 Nov: Alpaca removed the OCO stop ~0.2 s
        after a partial take-profit fill while 1 of 20 shares was still held).
        The take-profit remainder holds the shares, so a new stop can't be
        placed until it is gone. Steps, each from fresh broker data:
          1. confirm a working take-profit with filled_qty > 0 for this symbol
             (and, by being here, stop cover below the position);
          2. cancel the take-profit remainder (one request; a cancel may be
             repeated - it is idempotent and never a sell);
          3. wait for the take-profit to reach a terminal status (cancelled or
             filled) - a full fill during the cancel simply closes the position;
          4. then hand back to the normal restore path, which re-reads the
             position and places an OCO for the confirmed REMAINING, AVAILABLE
             quantity and confirms both legs.
        Returns True while this recovery owns the step (no other order sent)."""
        rows = [o for o in (orders or []) if o.get("symbol") == sym and o.get("side") == "sell"]
        if t.ptp is None:
            tp = next((o for o in rows if o.get("group_role") == "take_profit"
                       and o.get("status") in WORKING_STATUSES and (o.get("filled_qty") or 0) > 0), None)
            if tp is None:
                return False
            t.ptp = {"tp": tp["id"], "at": now, "tries": 0, "filled": tp.get("filled_qty"),
                     "qty": tp.get("qty")}
            self.alerts.raise_(f"prot_partial_tp:{sym}",
                               f"{sym}: take-profit partly filled ({tp.get('filled_qty'):g} of {tp.get('qty'):g}) "
                               f"and the remaining {pos.get('qty'):g} share(s) have no stop. Cancelling the "
                               "take-profit remainder, then placing a stop for the shares still held.",
                               "critical", 3600)
            logger.warning("Partial take-profit recovery for %s started: %s", sym, t.ptp)
            return self._ptp_cancel(sym, t, now)
        tp = next((o for o in rows if o.get("id") == t.ptp["tp"]), None)
        if tp is None or tp.get("status") not in WORKING_STATUSES:
            # Terminal (cancelled / filled / replaced). Hand back to the normal
            # restore path with the position as re-read this sweep.
            logger.warning("Partial take-profit recovery for %s: take-profit %s is %s; restoring "
                           "protection for the %g share(s) still held", sym, t.ptp["tp"],
                           (tp or {}).get("status", "gone"), pos.get("qty") or 0)
            t.ptp = None
            t.missing_since = t.missing_since or now
            t.attempts = []                      # the take-profit held the shares; not a failed restore
            return False
        if now - t.ptp["at"] >= self.PTP_ESCALATE_S:
            self._escalate(sym, t, now, f"take-profit remainder {t.ptp['tp']} still working "
                                        f"{self.PTP_ESCALATE_S:.0f} s after cancelling")
            return True
        if now - t.ptp.get("sent_at", t.ptp["at"]) >= self.PTP_RETRY_CANCEL_S:
            return self._ptp_cancel(sym, t, now)
        return True

    def _ptp_cancel(self, sym: str, t: Track, now: float) -> bool:
        t.ptp["tries"] += 1
        t.ptp["sent_at"] = now
        try:
            self.broker.cancel_order_once(t.ptp["tp"], f"cancel partly filled take-profit {sym}")
        except Exception as exc:  # noqa: BLE001
            # Ambiguous: nothing else is sent; the next sweep re-reads the order.
            logger.error("Cancel of partly filled take-profit %s for %s: outcome unknown (%s)",
                         t.ptp["tp"], sym, exc)
        return True

    def _restore(self, sym: str, t: Track, now: float, pos: dict, orders: Optional[List[dict]] = None) -> None:
        qty, covered = pos.get("qty") or 0.0, t.covered
        uncovered = round(qty - covered, 6)
        avail = pos.get("qty_available")
        if avail is not None and avail + EPS < uncovered:
            t.attempts.append({"at": now, "cid": None, "outcome": "shares held by another order"})
            self._attempt_failed(sym, t, f"only {avail:g} of {uncovered:g} uncovered shares are "
                                         "available (another order holds them)")
            return
        entry = pos.get("avg_entry_price") or 0.0
        stop = round(entry * (1.0 - self.risk.stop_loss_pct / 100.0), 2)
        tp_pct = (self.risk.trailing_stop_backstop_take_profit_pct if self.risk.trailing_stop_enabled
                  else self.risk.take_profit_pct)
        tp = round(entry * (1.0 + tp_pct / 100.0), 2)
        price = self.broker.latest_price(sym)
        if entry <= 0 or price is None:
            t.attempts.append({"at": now, "cid": None, "outcome": "no price"})
            self._attempt_failed(sym, t, "entry or current price unavailable")
            return
        if price <= stop:
            self._emergency_exit(sym, t, now, pos, orders or [], uncovered, price, stop)
            return
        fill = self.broker.last_fill(sym, side="buy")
        ref = position_ref(getattr(fill, "order_id", None), pos)
        prefix = order_id(self.env_tag, "P", sym, ref, 0)[:-1]
        hist = self.broker.symbol_orders_snapshot(sym)
        if not hist.ok:
            t.attempts.append({"at": now, "cid": None, "outcome": "order history unreadable"})
            self._attempt_failed(sym, t, f"order history unreadable ({hist.error})")
            return
        used, working = attempts_at_broker(hist.data, prefix)
        if working:
            # One of our protective orders is already working but doesn't
            # show as cover yet (e.g. still pending): wait, never duplicate.
            t.confirm_due = now + CONFIRM_AFTER_S
            return
        # Rejected-at-submission orders never exist at Alpaca, so also count
        # this process's own earlier attempts (keeps numbers increasing; a
        # different instance reusing a number Alpaca never stored is harmless).
        local = [int(a["cid"][len(prefix):]) for a in t.attempts
                 if a.get("cid") and a["cid"].startswith(prefix) and a["cid"][len(prefix):].isdigit()]
        cid = prefix + str(max([used] + local) + 1)
        if self.dry_run:
            logger.info("[DRY RUN] would restore protection %s: %g sh stop %.2f tp %.2f (%s)",
                        sym, uncovered, stop, tp, cid)
            t.attempts.append({"at": now, "cid": cid, "outcome": "dry_run"})
            t.escalated = True
            return
        try:
            self.broker.submit_oco_once(sym, uncovered, stop, tp, cid)
        except OrderBlockedError as exc:
            logger.warning("Protection restore for %s not sent: %s", sym, exc)
            return   # no lease: not an attempt; the holder will act
        except Exception as exc:  # noqa: BLE001
            kind = classify_submit_error(exc)
            if kind == "uncertain":
                logger.error("Protection restore %s for %s: outcome UNCERTAIN (%s); resolving by lookup.",
                             cid, sym, exc)
                t.uncertain = {"cid": cid, "at": now, "qty": uncovered, "stop": stop, "tp": tp}
                t.attempts.append({"at": now, "cid": cid, "outcome": "uncertain"})
                return
            t.attempts.append({"at": now, "cid": cid, "outcome": f"rejected: {exc}"})
            self._attempt_failed(sym, t, f"Alpaca rejected {cid}: {exc}")
            return
        logger.warning("Protection restore submitted for %s: %s (%g sh, stop %.2f, tp %.2f)",
                       sym, cid, uncovered, stop, tp)
        t.attempts.append({"at": now, "cid": cid, "outcome": "submitted"})
        t.confirm_due = now + CONFIRM_AFTER_S
        self.on_protected(sym)

    def _emergency_exit(self, sym: str, t: Track, now: float, pos: dict, orders: List[dict],
                        uncovered: float, price: float, stop: float) -> None:
        """Position CONFIRMED (fresh positions and orders reads) and already at
        or below the level its stop should be at, so a stop would fire at once.
        Policy (review 7 Nov):
          1. only with the exit manager enabled; otherwise escalate;
          2. the lease must be valid and fresh;
          3. no working sell order may already be acting on the symbol (never a
             duplicate sell) and the uncovered shares must be available;
          4. one deterministic X order for the uncovered quantity, single attempt;
          5. anything uncertain or refused -> escalate immediately, no retry.
        UNKNOWN broker state never reaches here (no orders on UNKNOWN)."""
        why = (f"price {price:.2f} is at or below the stop level {stop:.2f}; a protective stop "
               "would trigger at once")
        if t.emergency is not None:
            # One attempt per position. Still held after 60 s: escalate.
            if now - t.emergency["at"] >= 60:
                self._escalate(sym, t, now, f"emergency exit {t.emergency['cid']} sent but the "
                                            "position is still open")
            return
        if not self.emergency_exit_enabled:
            self._escalate(sym, t, now, why + " (emergency exit is disabled until the Stage 6 exit "
                                              "manager is enabled)")
            return
        guard = getattr(self.broker, "order_guard", None)
        if guard is not None:
            ok, reason = guard("exit")
            if not ok:
                self._escalate(sym, t, now, why + f"; emergency exit not allowed: {reason}")
                return
        conflicting = [o for o in orders if o.get("symbol") == sym and o.get("side") == "sell"
                       and o.get("status") in WORKING_STATUSES]
        if conflicting:
            self._escalate(sym, t, now, why + f"; {len(conflicting)} sell order(s) already working "
                                              f"({', '.join(o.get('client_order_id') or o.get('id') or '?' for o in conflicting)}) "
                                              "- not adding another")
            return
        avail = pos.get("qty_available")
        if avail is None or avail + EPS < uncovered:
            self._escalate(sym, t, now, why + f"; only {avail} of {uncovered:g} shares available")
            return
        cid = self.broker._next_exit_client_id("X", sym, pos)
        if cid is None:
            self._escalate(sym, t, now, why + f"; {getattr(self.broker, 'last_order_problem', '')}")
            return
        body = {"symbol": sym, "qty": str(uncovered), "side": "sell", "type": "market",
                "time_in_force": "day", "client_order_id": cid}
        try:
            self.broker.submit_order_once(body, "exit", f"emergency_exit({sym})")
        except Exception as exc:  # noqa: BLE001 - rejected, unknown or blocked: escalate, never retry
            t.emergency = {"cid": cid, "at": now}
            self._escalate(sym, t, now, why + f"; emergency exit {cid} failed: {exc}")
            return
        t.emergency = {"cid": cid, "at": now}
        t.attempts.append({"at": now, "cid": cid, "outcome": "emergency exit submitted"})
        logger.error("EMERGENCY EXIT %s: %g sh market sell %s (%s)", sym, uncovered, cid, why)
        self.alerts.raise_(f"prot_emergency:{sym}",
                           f"EMERGENCY EXIT submitted for {sym}: {uncovered:g} shares, market sell "
                           f"{cid}. Reason: {why}. Confirming at the broker.", "critical", 0)
        self.on_protected(sym)

    def _resolve_uncertain(self, sym: str, t: Track, now: float, pos: dict, orders: List[dict]) -> None:
        u = t.uncertain
        look = self.broker.order_by_client_id(u["cid"])
        if not look.ok:
            return  # still can't tell; wait for the next sweep
        if look.data is not None:
            t.uncertain = None
            status = (look.data.get("status") or "").lower()
            if status in WORKING_STATUSES or status == "filled":
                t.attempts[-1]["outcome"] = f"found after uncertainty ({status})"
                t.confirm_due = now + CONFIRM_AFTER_S
            else:
                t.attempts[-1]["outcome"] = f"found {status}"
                self._attempt_failed(sym, t, f"{u['cid']} ended {status}")
            return
        # Alpaca says it doesn't exist. Resubmit the SAME id only after 60 s
        # and only if nothing else suggests an order is working.
        if now - u["at"] < 60:
            return
        if any(o.get("symbol") == sym and o.get("side") == "sell" and o.get("status") in WORKING_STATUSES
               for o in orders) and t.covered > EPS:
            return
        try:
            self.broker.submit_oco_once(sym, u["qty"], u["stop"], u["tp"], u["cid"])
            t.uncertain, t.confirm_due = None, now + CONFIRM_AFTER_S
            t.attempts[-1]["outcome"] = "resubmitted same id"
            self.on_protected(sym)
        except OrderBlockedError:
            return
        except Exception as exc:  # noqa: BLE001
            if classify_submit_error(exc) == "uncertain":
                u["at"] = now
                return
            t.uncertain = None
            t.attempts[-1]["outcome"] = f"rejected on resubmit: {exc}"
            self._attempt_failed(sym, t, f"resubmit of {u['cid']} rejected: {exc}")

    def _attempt_failed(self, sym: str, t: Track, why: str) -> None:
        n = len(t.attempts)
        logger.error("Protection restore attempt %d for %s failed: %s", n, sym, why)
        self.alerts.raise_(f"prot_failed:{sym}",
                           f"{sym}: protection restore attempt {n} FAILED ({why}). Retrying "
                           f"({max(0, 3 - n)} attempts left).", "critical", 0)

    def _escalate(self, sym: str, t: Track, now: float, why: Optional[str]) -> None:
        t.escalated = True
        if why:
            t.attempts.append({"at": now, "cid": None, "outcome": f"escalated: {why}"})
        since = int((now - (t.missing_since or now)) / 60)
        self.alerts.raise_(f"prot_manual:{sym}",
                           f"MANUAL ACTION NEEDED: {sym} has been without full stop protection for "
                           f"{since} min ({why or 'restore attempts exhausted'}). Covered "
                           f"{t.covered:g} shares. Check the position in the Alpaca dashboard.",
                           "critical", 3600)
        if self.heartbeat is not None:
            self.heartbeat.fail(f"{sym} unprotected: manual action needed")

    # ---- background loop -----------------------------------------------------------
    def _in_hours(self) -> bool:
        t = self.now_local()
        if t.weekday() >= 5:
            return False
        m = t.hour * 60 + t.minute
        return 9 * 60 + 30 <= m <= 16 * 60

    def _due(self, now: float) -> bool:
        if now >= self.next_sweep_at:
            return True
        for t in self.tracks.values():
            if t.state in (UNPROTECTED, UNDER_PROTECTED) and not t.escalated:
                return True   # follow-ups every tick while a restore is in progress
        return False

    def tick(self) -> None:
        now = self.clock()
        if not self.active() or not self._due(now):
            return
        if not self.lock.acquire(timeout=1.0):
            return   # a trading cycle is running; it sweeps at its own start
        try:
            self.sweep()
        finally:
            self.lock.release()
        self.next_sweep_at = now + (self.sweep_s if self._in_hours() else self.off_hours_sweep_s)

    def start(self, tick_s: float = 15.0) -> None:
        def _loop():
            while not self._stop.wait(tick_s):
                try:
                    self.tick()
                except Exception as exc:  # noqa: BLE001
                    logger.error("Protection sweep error: %s", exc)
        threading.Thread(target=_loop, daemon=True, name="protection-sweep").start()

    def stop(self) -> None:
        self._stop.set()
