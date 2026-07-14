"""Reads the currently-active strategy version from the dashboard's
`strategy_versions` table, so every trade gets tagged with whichever version
was live at entry time.

This is a read-only mirror of bot/notifications/settings.py's caching
pattern: a short in-memory cache (the bot runs a cycle every 30 min, so a
60s cache means a new version created on the dashboard takes effect on the
very next cycle, not the next deploy) with a safe fallback to "v1" if the
database is unset or unreachable, so the bot never blocks on this.

IMPORTANT: this module only ever READS. Creating a new strategy version is
exclusively a dashboard action (see dashboard/app/api/strategy-versions),
driven by a human approving a recommendation - the bot never creates or
activates a version itself. This keeps the "AI never modifies trading
behaviour automatically" guarantee intact: even though the ACTIVE version
can change between deploys, that change was a deliberate human action taken
on the dashboard, not something this module or the trading engine decided.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger("bot.strategy_version")

CACHE_TTL_SECONDS = 60
FALLBACK_VERSION = "v1"


class StrategyVersionProvider:
    def __init__(self, database_url: Optional[str] = None):
        self.database_url = database_url
        self._cached_version: str = FALLBACK_VERSION
        self._cache_ts: float = 0.0

    def _refresh(self) -> None:
        if not self.database_url:
            return
        try:
            import psycopg2

            conn = psycopg2.connect(self.database_url, connect_timeout=5)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT version FROM strategy_versions WHERE is_active = true "
                        "ORDER BY deployed_at DESC LIMIT 1"
                    )
                    row = cur.fetchone()
                if row and row[0]:
                    self._cached_version = row[0]
                self._cache_ts = time.time()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not refresh active strategy version (using cache/fallback): %s", exc)

    def current_version(self) -> str:
        if self.database_url and (time.time() - self._cache_ts) > CACHE_TTL_SECONDS:
            self._refresh()
        return self._cached_version
