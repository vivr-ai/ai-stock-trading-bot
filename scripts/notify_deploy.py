#!/usr/bin/env python3
"""Sends a "Railway deployment completed" Telegram notification.

Intended to run as Railway's `releaseCommand` (see railway.toml), which
Railway runs once per deploy, after the build succeeds and before the new
instance takes traffic - so this only fires on an actual deploy, not on a
process crash-restart (which the bot's own bot_restart / scheduler_failure
notifications already cover).

Requires no Railway API token: Railway automatically injects
RAILWAY_GIT_COMMIT_SHA, RAILWAY_ENVIRONMENT_NAME, and RAILWAY_SERVICE_NAME
into every service's environment - this script just reads them.

Safe to run with Telegram/DB unconfigured: it just logs and exits 0, since a
notification failure should never fail a deploy.
"""
from __future__ import annotations

import os
import sys

# Allow running as `python scripts/notify_deploy.py` from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.notifications import NotificationService, TelegramNotifier  # noqa: E402
from bot.persistence import Recorder  # noqa: E402


def main() -> int:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    telegram = TelegramNotifier(bot_token, chat_id)

    commit = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "unknown")[:7]
    env = os.environ.get("RAILWAY_ENVIRONMENT_NAME", "unknown")
    service = os.environ.get("RAILWAY_SERVICE_NAME", "unknown")
    author = os.environ.get("RAILWAY_GIT_AUTHOR", "")
    message = os.environ.get("RAILWAY_GIT_COMMIT_MESSAGE", "")

    text = (
        f"service={service} env={env} commit={commit}"
        + (f" by {author}" if author else "")
        + (f"\n{message.strip()}" if message else "")
    )

    notifier = NotificationService(Recorder(), telegram=telegram)
    notifier.record_notification(
        type_="deployment_completed", severity="info",
        title="Railway deployment completed", message=text,
    )
    print(f"notify_deploy: recorded deployment_completed ({text})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
