"""Cycle watchdog: alerts if the trading scheduler stops running cycles.

`mark()` is called at the start of every scheduled cycle (including the
"market closed" ones on holidays, since the scheduler still fires). A
background thread checks every few minutes; during weekday trading hours
(09:35-16:05 ET) a gap longer than `max_gap_seconds` (default 65 min, a bit
over two 30-minute cycles) sends a critical Telegram alert, repeated hourly,
plus a recovery notice when cycles resume.

Limits: this runs inside the bot process, so it detects a stalled or crashed
scheduler, not a dead process. A process that dies is restarted by Railway
(restart policy) and announces itself on start-up; an external uptime check
would be needed to catch a process that is down and not restarting.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class CycleWatchdog:
    def __init__(self, alert: Callable[[str, str], None], timezone: str = "America/New_York",
                 max_gap_seconds: float = 65 * 60, repeat_seconds: float = 3600,
                 clock: Callable[[], float] = time.time,
                 now_local: Optional[Callable[[], datetime]] = None):
        from zoneinfo import ZoneInfo

        self.alert = alert
        self.tz = ZoneInfo(timezone)
        self.max_gap = max_gap_seconds
        self.repeat = repeat_seconds
        self.clock = clock
        self.now_local = now_local or (lambda: datetime.now(self.tz))
        self.last_cycle = clock()
        self._alerting = False
        self._last_alert = 0.0
        self._stop = threading.Event()

    def mark(self) -> None:
        if self._alerting:
            self._alerting = False
            self._send("Trading cycles have resumed.", "warning")
        self.last_cycle = self.clock()

    def _in_trading_hours(self) -> bool:
        t = self.now_local()
        if t.weekday() >= 5:
            return False
        minutes = t.hour * 60 + t.minute
        return 9 * 60 + 35 <= minutes <= 16 * 60 + 5

    def check_once(self) -> None:
        if not self._in_trading_hours():
            return
        now = self.clock()
        gap = now - self.last_cycle
        if gap < self.max_gap:
            return
        if not self._alerting or now - self._last_alert >= self.repeat:
            self._alerting, self._last_alert = True, now
            self._send(f"No trading cycle has run for {int(gap // 60)} minutes during market hours. "
                       "The scheduler may be stalled; check the Railway service.", "critical")

    def start(self, interval_seconds: float = 300) -> None:
        def _loop():
            while not self._stop.wait(interval_seconds):
                try:
                    self.check_once()
                except Exception as exc:  # noqa: BLE001
                    logger.error("Watchdog check failed: %s", exc)
        threading.Thread(target=_loop, daemon=True, name="cycle-watchdog").start()

    def stop(self) -> None:
        self._stop.set()

    def _send(self, text: str, severity: str) -> None:
        try:
            self.alert(text, severity)
        except Exception as exc:  # noqa: BLE001
            logger.error("Watchdog alert could not be sent: %s", exc)
