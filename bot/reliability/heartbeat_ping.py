"""External heartbeat (Reliability plan v3, 2E): Healthchecks.io pings.

The in-process watchdog can't notice the process itself dying. This sends a
ping to an outside service after each GENUINELY successful trading cycle; the
service alerts (email / Telegram, configured there) when pings stop arriving
on schedule. It depends on neither this process nor the database being up.

What counts as success (decided by the strategy, see run_cycle):
  * the cycle completed and the broker account could be read, or
  * the market was closed (holiday) and the cycle correctly did nothing.
A cycle that aborted (e.g. account snapshot failed) or raised sends /fail.
Nothing is sent just because the process is alive.

Schedule (set in Healthchecks.io, not here): cron `0,30 9-15 * * 1-5`,
time zone America/New_York, grace 15 minutes. Weekends expect nothing. US
market holidays still get pings: the scheduler runs, sees the market is
closed, and reports a successful no-op cycle.

Disabled (no network calls at all) when EXTERNAL_HEARTBEAT_URL is empty. The
URL is a secret-ish token; it is never logged in full.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def _mask(url: str) -> str:
    return url[:24] + "..." if len(url) > 24 else "(set)"


class ExternalHeartbeat:
    def __init__(self, url: str, post: Optional[Callable] = None, timeout_s: float = 5.0,
                 clock: Callable[[], float] = time.time, min_fail_interval_s: float = 600.0):
        self.url = (url or "").strip().rstrip("/")
        self.timeout_s = timeout_s
        self.clock = clock
        self.min_fail_interval_s = min_fail_interval_s
        self._last_fail_sent = 0.0
        self._failing = False
        self._post = post
        self._lock = threading.Lock()
        if self.enabled:
            logger.info("External heartbeat enabled (%s)", _mask(self.url))
        else:
            logger.info("External heartbeat disabled (EXTERNAL_HEARTBEAT_URL not set)")

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def success(self, note: str = "") -> bool:
        """Ping after a genuinely successful cycle. Never raises."""
        if not self.enabled:
            return False
        ok = self._send(self.url, note or "cycle ok")
        if ok:
            with self._lock:
                self._failing = False
        return ok

    def fail(self, reason: str) -> bool:
        """Report a failed cycle (Healthchecks marks the check down and
        alerts once). Repeated failures are sent at most every 10 minutes so
        one stuck fault doesn't spam the service."""
        if not self.enabled:
            return False
        now = self.clock()
        with self._lock:
            if self._failing and now - self._last_fail_sent < self.min_fail_interval_s:
                return False
            self._failing, self._last_fail_sent = True, now
        return self._send(self.url + "/fail", reason[:1000])

    def _send(self, url: str, body: str) -> bool:
        try:
            if self._post is not None:
                status = self._post(url, body, self.timeout_s)
            else:
                import requests

                status = requests.post(url, data=body.encode("utf-8"), timeout=self.timeout_s).status_code
            if status and 200 <= int(status) < 300:
                return True
            logger.warning("External heartbeat ping returned HTTP %s", status)
            return False
        except Exception as exc:  # noqa: BLE001 - monitoring must never break trading
            logger.warning("External heartbeat ping failed: %s", exc)
            return False
