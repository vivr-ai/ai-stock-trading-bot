"""DB health gate: decides whether NEW entries are allowed this cycle.

States
  reconciling  start-up state, and after the DB comes back. Entries blocked
               until a reconciliation pass (see reconcile.py) succeeds.
  healthy      DB reachable and reconciled. Entries allowed (subject to every
               existing gate: pause, daily loss limit, market filters).
  down         DB unreachable. Entries blocked. Exits keep running (they use
               broker data when the DB can't answer).

Alerts go straight to Telegram (never via the DB, which is what's broken):
critical on the first failure, a reminder every hour while it lasts, a
warning if reconciliation fails, and a notice when entries are re-enabled.

The gate never touches broker orders: protective stop / take-profit legs stay
exactly as they are in every state.

With no DATABASE_URL configured at all (local dev), the gate is a no-op and
entries behave exactly as before.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

RECONCILING, HEALTHY, DOWN = "reconciling", "healthy", "down"


class DbHealthGate:
    def __init__(self, recorder, alert: Callable[[str, str], None],
                 reconcile: Optional[Callable[[], Tuple[bool, str]]] = None,
                 repeat_alert_seconds: float = 3600.0, clock: Callable[[], float] = time.time):
        self.recorder = recorder
        self.alert = alert
        self.reconcile = reconcile or (lambda: (True, "no reconciliation configured"))
        self.repeat_alert_seconds = repeat_alert_seconds
        self.clock = clock
        self.enabled = bool(getattr(recorder, "enabled", False))
        self.state = RECONCILING if self.enabled else HEALTHY
        self.down_since: Optional[float] = None
        self._last_alert_at = 0.0
        self._last_reconcile_alert_at = 0.0
        self.last_reason = "starting up: reconciliation pending" if self.enabled else "no database configured"

    # ---- called once at the start of every cycle ---------------------------
    def check(self) -> bool:
        """Run the health check (and reconciliation when needed). Returns
        True if new entries are allowed this cycle."""
        if not self.enabled:
            return True
        begin = getattr(self.recorder, "begin_cycle", None)
        if callable(begin):
            begin()
        try:
            ok = bool(self.recorder.health_check())
        except Exception as exc:  # noqa: BLE001
            ok = False
            self.recorder.last_error = str(exc)
        if not ok:
            self._mark_down(getattr(self.recorder, "last_error", None) or "health check failed")
            return False
        if getattr(self.recorder, "critical_failed", False) and self.state == HEALTHY:
            # A critical write failed since the last check: reconcile before
            # entries resume, even though the DB answers again.
            self._mark_down(getattr(self.recorder, "last_error", None) or "critical write failed")
        if self.state in (DOWN, RECONCILING):
            self._try_reconcile()
        return self.state == HEALTHY

    # ---- cheap re-check before an order (no DB call) -----------------------
    def entries_allowed(self) -> bool:
        if not self.enabled:
            return True
        if hasattr(self.recorder, "critical_failed"):
            # v3 2G: only a failed CRITICAL write (trades, closed trades,
            # trailing/stagnation state) blocks entries; analytics writes
            # are retried and journalled without interrupting trading.
            failed = bool(self.recorder.critical_failed)
        else:
            failed = not getattr(self.recorder, "healthy", True)
        if failed:
            # A critical write failed mid-cycle: stop entries until the DB is
            # healthy AND a reconciliation pass succeeds.
            self._mark_down(getattr(self.recorder, "last_error", None) or "write failed mid-cycle")
            return False
        return self.state == HEALTHY

    # ---- internals ------------------------------------------------------------
    def _mark_down(self, error: str) -> None:
        now = self.clock()
        if self.state != DOWN:
            self.state, self.down_since, self._last_alert_at = DOWN, now, now
            self.last_reason = f"database unreachable: {error}"
            logger.error("DB health gate: DOWN (%s) - new entries blocked", error)
            self._send(
                "DATABASE UNREACHABLE - new entries are BLOCKED.\n"
                "Existing broker stop-loss / take-profit orders are untouched, and exits keep "
                "running on Alpaca data.\n"
                f"Error: {error}", "critical")
        elif now - self._last_alert_at >= self.repeat_alert_seconds:
            self._last_alert_at = now
            mins = int((now - (self.down_since or now)) // 60)
            self._send(f"Database still unreachable (down {mins // 60}h {mins % 60}m). "
                       "New entries remain blocked.", "critical")

    def _try_reconcile(self) -> None:
        # Announce the recovery if entries were blocked by an outage or a
        # failed reconciliation (not on a quiet, successful start-up).
        was_down = self.down_since is not None or self._last_reconcile_alert_at > 0
        try:
            ok, summary = self.reconcile()
        except Exception as exc:  # noqa: BLE001
            ok, summary = False, f"reconciliation raised: {exc}"
        now = self.clock()
        if ok:
            self.state, self.last_reason = HEALTHY, "healthy"
            if getattr(self.recorder, "critical_failed", False):
                self.recorder.critical_failed = False
            logger.info("DB health gate: HEALTHY (%s)", summary)
            if was_down:
                if self.down_since is not None:
                    mins = int((now - self.down_since) // 60)
                    head = f"Database recovered after {mins // 60}h {mins % 60}m. "
                else:
                    head = ""
                self._send(f"{head}Reconciliation complete: {summary}. New entries re-enabled.", "warning")
            self.down_since = None
            self._last_reconcile_alert_at = 0.0
            return
        self.state, self.last_reason = RECONCILING, f"reconciliation failed: {summary}"
        logger.error("DB health gate: reconciliation failed (%s) - entries stay blocked", summary)
        if self._last_reconcile_alert_at == 0.0 or now - self._last_reconcile_alert_at >= self.repeat_alert_seconds:
            self._last_reconcile_alert_at = now
            self._send(f"Database reachable but reconciliation FAILED: {summary}. "
                       "New entries remain blocked.", "critical")

    def _send(self, text: str, severity: str) -> None:
        try:
            self.alert(text, severity)
        except Exception as exc:  # noqa: BLE001 - alerting must never break a cycle
            logger.error("DB health alert could not be sent: %s", exc)
