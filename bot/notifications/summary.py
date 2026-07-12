"""Rolls up notifications configured for 'daily_summary' / 'weekly_summary'
delivery into a short text block appended to the actual daily/weekly Telegram
summary message.

Why this exists: setting a notification type's channel to "daily_summary" in
the dashboard's Notification Settings page suppresses its immediate Telegram
send (see bot/notifications/service.py) - but on its own that just makes the
event go quiet everywhere except the Notifications Centre. This module is
what actually surfaces it again, once a day, as part of the summary Telegram
message that's already sent at end-of-day / end-of-week.

Best-effort throughout: any DB problem here returns an empty string rather
than raising, so a rollup query failing never blocks the daily/weekly
summary itself from being sent.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("bot.notifications.summary")

# Notification types that are themselves summaries - never roll these into
# their own summary (would be self-referential / noise).
_EXCLUDED_TYPES = ("daily_summary", "weekly_summary")

# Human-readable labels, matching db/schema.sql's seeded notification_settings.
_LABELS = {
    "bot_restart": "Bot started / restarted",
    "bot_stopped_unexpectedly": "Bot stopped unexpectedly",
    "deployment_completed": "Deployments",
    "trade_executed": "Trades executed",
    "daily_loss_limit": "Daily loss limit reached",
    "broker_issue": "Broker/API connection issues",
    "database_failure": "Database failures",
    "scheduler_failure": "Scheduler failures",
    "error": "Errors",
}


def _fetch_queued(database_url: str, channel: str, window_sql: str) -> list:
    import psycopg2

    conn = psycopg2.connect(database_url, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT n.type, count(*) as cnt, max(n.ts) as last_ts,
                       (array_agg(n.title ORDER BY n.ts DESC))[1] as last_title
                FROM notifications n
                JOIN notification_settings s ON s.type = n.type
                WHERE s.channel = %(channel)s
                  AND s.enabled = true
                  AND n.ts >= {window_sql}
                  AND n.type NOT IN %(excluded)s
                GROUP BY n.type
                ORDER BY cnt DESC
                """,
                dict(channel=channel, excluded=_EXCLUDED_TYPES),
            )
            return cur.fetchall()
    finally:
        conn.close()


def build_queued_summary(database_url: Optional[str], channel: str, *, period_label: str) -> str:
    """Returns a formatted text block (possibly empty) listing what's been
    queued for this channel ('daily_summary' or 'weekly_summary') since the
    relevant window. `period_label` is just used in the heading text
    ('today' / 'this week')."""
    if not database_url:
        return ""

    window_sql = "now() - interval '1 day'" if channel == "daily_summary" else "now() - interval '7 days'"

    try:
        rows = _fetch_queued(database_url, channel, window_sql)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not build queued notification summary for %s: %s", channel, exc)
        return ""

    if not rows:
        return ""

    lines = [f"Queued alerts {period_label} (set to summary-only, not sent immediately):"]
    for type_, cnt, _last_ts, last_title in rows:
        label = _LABELS.get(type_, type_)
        suffix = f" — most recent: {last_title}" if cnt == 1 and last_title else ""
        lines.append(f"- {label}: {cnt}{suffix}")
    return "\n".join(lines)
