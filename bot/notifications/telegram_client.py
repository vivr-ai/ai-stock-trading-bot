"""Minimal Telegram Bot API client for outbound notifications only.

Deliberately tiny: one method (`send`), no polling/webhook receiver (the bot
never needs to read Telegram messages, only send them). Uses the shared
`requests` dependency already in requirements.txt — no new package needed.

Design goals, matching the rest of the codebase's reliability posture:
  * Never raises into the caller. A Telegram outage should never affect
    trading or even crash a notification call site — log and move on.
  * Short timeout + few retries (this is a "best effort" side channel, not
    a critical path — we don't want a flaky Telegram API to delay a trading
    cycle).
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("bot.notifications.telegram")

TELEGRAM_API_BASE = "https://api.telegram.org"
# Telegram message cap is 4096 UTF-16 code units; stay comfortably under it.
MAX_MESSAGE_LEN = 3800


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, *, timeout: float = 8.0,
                 attempts: int = 2):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout
        self.attempts = attempts
        self.enabled = bool(bot_token and chat_id)
        if not self.enabled:
            logger.info(
                "Telegram notifier disabled (TELEGRAM_BOT_TOKEN / "
                "TELEGRAM_CHAT_ID not both set)."
            )

    def send(self, text: str, *, severity: str = "info") -> bool:
        """Send a message. Returns True on success, False otherwise (never
        raises). `severity` just controls a small prefix emoji so alerts are
        scannable at a glance in the Telegram chat."""
        if not self.enabled:
            return False
        if not text:
            return False

        prefix = {"critical": "\U0001F6A8 ", "warning": "⚠️ ", "info": ""}.get(
            severity, ""
        )
        body = f"{prefix}{text}"
        if len(body) > MAX_MESSAGE_LEN:
            body = body[: MAX_MESSAGE_LEN - 20] + "\n… (truncated)"

        import requests  # local import: keep this module importable even if
        # `requests` isn't installed in an environment that never uses Telegram

        url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": body, "disable_web_page_preview": True}

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.attempts + 1):
            try:
                resp = requests.post(url, json=payload, timeout=self.timeout)
                if resp.status_code == 200:
                    return True
                logger.warning(
                    "Telegram send failed (attempt %d/%d): HTTP %d %s",
                    attempt, self.attempts, resp.status_code, resp.text[:300],
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("Telegram send error (attempt %d/%d): %s",
                                attempt, self.attempts, exc)
        if last_exc:
            logger.warning("Telegram notification dropped after %d attempts: %s",
                            self.attempts, last_exc)
        return False

    def test(self) -> bool:
        """Send a one-off connectivity test message. Used by `main.py --check`
        and can be called manually to verify bot token / chat id are correct."""
        return self.send(
            "Telegram connectivity check from the trading bot ✅", severity="info"
        )
