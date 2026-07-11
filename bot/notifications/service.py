"""Wires the Postgres Recorder + Telegram + notification_settings together.

This is a drop-in replacement for a bare `Recorder` at every existing call
site (`bot/trading/strategy.py`, `main.py`, ...): it proxies every method
Recorder has (record_heartbeat, record_decision, record_trade, ...)
unchanged, and only adds behaviour on top of `record_notification` plus a
health check that piggybacks on every proxied call. No existing call site
needs to change.

Responsibilities added on top of the plain DB-only Recorder:
  1. record_notification(...) - after writing the audit row to Postgres (as
     before), also sends it to Telegram if that notification type's
     configured channel is "immediate".
  2. DB outage detection - after any proxied Recorder call, checks
     `recorder.healthy`. On a transition to unhealthy, sends a rate-limited
     Telegram alert directly (bypassing the DB, which is exactly what's
     down) so a Postgres outage is never silent. Sends a short "recovered"
     note on the way back up.
  3. notify_bot_stopped_unexpectedly / notify_scheduler_failure /
     notify_critical_error - convenience wrappers for call sites that don't
     have a fully-built Recorder/DB path available (e.g. scheduler crash
     handling), used directly by main.py / bot/scheduler.py.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from .settings import NotificationSettings
from .telegram_client import TelegramNotifier

logger = logging.getLogger("bot.notifications.service")

DB_ALERT_COOLDOWN_SECONDS = 15 * 60  # don't spam Telegram every failed row


class NotificationService:
    def __init__(self, recorder, telegram: Optional[TelegramNotifier] = None,
                 settings: Optional[NotificationSettings] = None):
        self._recorder = recorder
        self.telegram = telegram or TelegramNotifier("", "")
        self.settings = settings or NotificationSettings(
            getattr(recorder, "database_url", None)
        )
        self._last_db_alert_ts: float = 0.0
        self._db_alert_active: bool = False

    # ---- proxy every other Recorder method unchanged -------------------
    def __getattr__(self, name: str):
        attr = getattr(self._recorder, name)
        if not callable(attr):
            return attr

        def wrapped(*args, **kwargs):
            result = attr(*args, **kwargs)
            self._check_db_health()
            return result

        return wrapped

    # ---- DB outage detection --------------------------------------------
    def _check_db_health(self) -> None:
        recorder = self._recorder
        if not getattr(recorder, "enabled", False):
            return  # no DATABASE_URL configured at all - not an "outage"
        healthy = getattr(recorder, "healthy", True)
        now = time.time()
        if not healthy and not self._db_alert_active:
            if now - self._last_db_alert_ts >= DB_ALERT_COOLDOWN_SECONDS:
                self._last_db_alert_ts = now
                self._db_alert_active = True
                self.telegram.send(
                    "Database write failing — dashboard persistence is degraded. "
                    f"Trading continues unaffected. Last error: "
                    f"{getattr(recorder, 'last_error', 'unknown')}",
                    severity="critical",
                )
        elif healthy and self._db_alert_active:
            self._db_alert_active = False
            self.telegram.send("Database connectivity recovered.", severity="info")

    # ---- the one method with real added behaviour -----------------------
    def record_notification(self, *, type_: str, title: str, message: Optional[str] = None,
                             severity: str = "info", metadata: Optional[Dict[str, Any]] = None) -> None:
        # Always keep the audit trail in Postgres (unchanged behaviour).
        self._recorder.record_notification(
            type_=type_, title=title, message=message, severity=severity, metadata=metadata,
        )
        self._check_db_health()

        channel = self.settings.channel_for(type_)
        if channel != "immediate":
            logger.debug("Notification '%s' queued for %s (not sent immediately).",
                         type_, channel)
            return

        text = title if not message else f"{title}\n{message}"
        self.telegram.send(text, severity=severity)

    # ---- convenience wrappers for call sites without a full cycle -------
    def notify_bot_stopped_unexpectedly(self, reason: str) -> None:
        self.record_notification(
            type_="bot_stopped_unexpectedly", severity="critical",
            title="Bot stopped unexpectedly", message=reason,
        )

    def notify_scheduler_failure(self, reason: str) -> None:
        self.record_notification(
            type_="scheduler_failure", severity="critical",
            title="Scheduler crashed", message=reason,
        )

    def notify_critical_error(self, where: str, reason: str) -> None:
        self.record_notification(
            type_="error", severity="critical",
            title=f"Critical error in {where}", message=reason,
        )
