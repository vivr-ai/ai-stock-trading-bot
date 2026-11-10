"""De-duplicated, rate-limited alerts (Reliability plan v3, 2G).

Every reliability component raises alerts through one AlertDeduper so a
persistent fault produces one message, then a reminder at a fixed interval,
then one recovery message - never a storm.

    alerts.raise_("db_down", "Database unreachable ...", "critical", repeat_s=3600)
    alerts.resolve("db_down", "Database recovered ...")

Rules
  * The first raise of a key is sent immediately.
  * Further raises of the same key are suppressed until `repeat_s` has passed
    (per key; default 1 hour), then sent again as a reminder.
  * resolve() sends its message only if the key was active, then clears it.
  * The daily cap limits REMINDERS only: at most `daily_cap` reminder
    messages per UTC day across all keys. When it is reached a single
    "alert storm" notice is sent and further reminders are logged only.
  * A NEW problem (first raise of a key) and a recovery message are never
    silenced by the reminder cap (staging, 9 Nov: a 15-minute VZ reminder
    used up the cap and would have hidden any new emergency). Non-critical
    new problems have their own, larger cap (`new_cap`); critical new
    problems are always sent.
Sending never raises: alerting must not break a trading cycle.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


class AlertDeduper:
    def __init__(self, send: Callable[[str, str], None], clock: Callable[[], float] = time.time,
                 default_repeat_s: float = 3600.0, daily_cap: int = 20, new_cap: int = 60):
        self._send_raw = send
        self.clock = clock
        self.default_repeat_s = default_repeat_s
        self.daily_cap = daily_cap
        self.new_cap = new_cap
        self._new_today = 0
        self._active: Dict[str, float] = {}   # key -> time last sent
        self._day = None
        self._sent_today = 0
        self._storm_notified = False
        self._lock = threading.Lock()

    def is_active(self, key: str) -> bool:
        return key in self._active

    def raise_(self, key: str, text: str, severity: str = "warning",
               repeat_s: Optional[float] = None) -> bool:
        """Returns True if a message was actually sent."""
        now = self.clock()
        repeat = self.default_repeat_s if repeat_s is None else repeat_s
        with self._lock:
            last = self._active.get(key)
            if last is not None and now - last < repeat:
                return False
            self._active[key] = now
        return self._deliver(text, severity, key, reminder=last is not None)

    def resolve(self, key: str, text: Optional[str] = None, severity: str = "warning") -> bool:
        with self._lock:
            was_active = self._active.pop(key, None) is not None
        if was_active and text:
            return self._deliver(text, severity, key + ":resolved", reminder=False, recovery=True)
        return False

    def _deliver(self, text: str, severity: str, key: str, reminder: bool = True,
                 recovery: bool = False) -> bool:
        day = datetime.fromtimestamp(self.clock(), tz=timezone.utc).date()
        with self._lock:
            if day != self._day:
                self._day, self._sent_today, self._new_today = day, 0, 0
                self._storm_notified = False
            if reminder:
                capped = self._sent_today >= self.daily_cap
                if not capped:
                    self._sent_today += 1
            elif recovery or severity == "critical":
                capped = False                      # never silenced
            else:
                capped = self._new_today >= self.new_cap
                if not capped:
                    self._new_today += 1
            storm = None
            if capped:
                storm = not self._storm_notified
                self._storm_notified = True
        if storm is None:
            return self._safe_send(text, severity)
        log = logger.error if severity == "critical" else logger.warning
        log("Alert suppressed by daily cap [%s]: %s", key, text)
        if storm:
            self._safe_send("Alert storm: too many repeat alerts today. Further REMINDERS are logged "
                            "only until tomorrow (UTC); new problems and recoveries are still sent. "
                            "Check the Railway logs.", "critical")
        return False

    def _safe_send(self, text: str, severity: str) -> bool:
        try:
            self._send_raw(text, severity)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Alert could not be sent (%s): %s", exc, text)
            return False
