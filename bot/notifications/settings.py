"""Per-notification-type delivery settings, configurable from the dashboard.

Each notification type has a `channel`:
  * "immediate" - send to Telegram as soon as the event happens (default for
    most types)
  * "daily_summary" - don't send immediately; just get counted/listed in the
    end-of-day Telegram summary
  * "weekly_summary" - same, but rolled into the Friday weekly summary
  * "off" - never sent to Telegram (still always recorded to the DB
    notifications table for the dashboard's Notifications Centre - this
    setting only controls the Telegram channel)

Settings live in the `notification_settings` table (see db/schema.sql) so
they can be edited from the dashboard without a redeploy. This module reads
them with a short in-memory cache (the bot runs a cycle every 30 min - a
60s cache means a settings change from the dashboard takes effect on the
very next cycle, not the next deploy) and falls back to sensible built-in
defaults if the DB is unset/unreachable, so Telegram notifications keep
working even without the dashboard's Postgres configured.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Optional

logger = logging.getLogger("bot.notifications.settings")

CACHE_TTL_SECONDS = 60

# Built-in defaults: which channel each notification type uses when the
# dashboard hasn't overridden it. Kept deliberately noisy-by-default for
# anything safety-related (errors, broker/db/scheduler issues, daily loss
# limit) and quieter for routine trade chatter, which is still fully visible
# in daily/weekly summaries and the Notifications Centre either way.
DEFAULT_CHANNELS: Dict[str, str] = {
    "bot_restart": "immediate",
    "bot_stopped_unexpectedly": "immediate",
    "deployment_completed": "immediate",
    "trade_executed": "immediate",
    "daily_summary": "immediate",
    "weekly_summary": "immediate",
    "daily_loss_limit": "immediate",
    "broker_issue": "immediate",
    "database_failure": "immediate",
    "scheduler_failure": "immediate",
    "error": "immediate",
}


class NotificationSettings:
    """Thin cached accessor over the notification_settings table."""

    def __init__(self, database_url: Optional[str] = None):
        self.database_url = database_url
        self._cache: Dict[str, str] = {}
        self._cache_ts: float = 0.0

    def _refresh(self) -> None:
        if not self.database_url:
            return
        try:
            import psycopg2

            conn = psycopg2.connect(self.database_url, connect_timeout=5)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT type, channel, enabled FROM notification_settings")
                    rows = cur.fetchall()
                fresh: Dict[str, str] = {}
                for type_, channel, enabled in rows:
                    fresh[type_] = channel if enabled else "off"
                self._cache = fresh
                self._cache_ts = time.time()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not refresh notification settings (using cache/defaults): %s", exc)

    def channel_for(self, type_: str) -> str:
        """Return 'immediate' | 'daily_summary' | 'weekly_summary' | 'off'
        for this notification type."""
        if self.database_url and (time.time() - self._cache_ts) > CACHE_TTL_SECONDS:
            self._refresh()
        return self._cache.get(type_, DEFAULT_CHANNELS.get(type_, "immediate"))
