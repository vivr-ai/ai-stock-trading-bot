"""Reads the dashboard's `bot_control` singleton row, so the trading engine
can honour a manual pause / Emergency Stop set from the dashboard.

This is a read-only mirror of bot/strategy_version.py's caching pattern: a
short in-memory cache (the bot runs a cycle every 30 min, so a 60s cache
means a pause/resume set on the dashboard takes effect on the very next
cycle) with a safe fallback if the database is unset or unreachable.

IMPORTANT: this module only ever READS `bot_control`. Setting is_paused is
exclusively a dashboard action (see dashboard/app/api/bot-control), driven
by a human clicking Pause/Resume/Emergency Stop - the bot never pauses or
resumes itself. This keeps the "trading behaviour only changes via an
explicit human action" guarantee intact.

The fallback on a DB outage is "not paused" (fail OPEN), not "paused"
(fail closed). That's deliberate: an unreachable dashboard database should
not silently halt trading - that's a different failure mode already
covered by the account-snapshot try/except and broker_issue notification in
bot/trading/strategy.py. A manual pause is a deliberate state a human set;
losing the ability to *read* that state should default to "keep trading
as before", not invent a new safety event out of a DB hiccup.

Pausing only blocks NEW entries. It runs through the exact same
`new_entries_allowed` gate already used for the daily loss limit and market
filters (see SentimentStrategy.run_cycle), so sentiment-driven sells and
broker-side stop-loss/take-profit brackets keep managing existing positions
while paused - pausing never leaves open positions unprotected.
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Tuple

logger = logging.getLogger("bot.bot_control")

CACHE_TTL_SECONDS = 60


class BotControlProvider:
    def __init__(self, database_url: Optional[str] = None):
        self.database_url = database_url
        self._cached_paused: bool = False
        self._cached_reason: Optional[str] = None
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
                        "SELECT is_paused, reason FROM bot_control WHERE id = 1"
                    )
                    row = cur.fetchone()
                if row is not None:
                    self._cached_paused = bool(row[0])
                    self._cached_reason = row[1]
                self._cache_ts = time.time()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not refresh bot_control state (using cache/fail-open): %s", exc
            )

    def is_paused(self) -> Tuple[bool, Optional[str]]:
        """Returns (is_paused, reason). Fails open (False, None) if the
        database is unset, unreachable, or has never been refreshed."""
        if self.database_url and (time.time() - self._cache_ts) > CACHE_TTL_SECONDS:
            self._refresh()
        return self._cached_paused, self._cached_reason
