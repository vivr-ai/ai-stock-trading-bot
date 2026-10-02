"""Minimal Telegram Bot API client for the investment tracker's own bot.

Deliberately not imported from bot/notifications/telegram_client.py (which
is the trading bot's own, separate Telegram bot/notifier) - this module
has zero shared imports or state with bot/, per the design doc's locked
"logically independent" decision. The two are intentionally similar in
style (same reliability posture: never raise, short timeout, a couple of
retries) since that pattern is already proven on Railway in this repo.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger("investing.telegram_client")

TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_MESSAGE_LEN = 3800  # Telegram's cap is 4096 UTF-16 code units; stay under it
TIMEOUT = 8.0
ATTEMPTS = 2


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)
        if not self.enabled:
            logger.info(
                "Investment-tracker Telegram notifier disabled "
                "(INVEST_TELEGRAM_BOT_TOKEN / INVEST_TELEGRAM_CHAT_ID not both set)."
            )

    def send(self, text: str) -> bool:
        """Send a message. Returns True on success, False otherwise. Never
        raises - a Telegram outage should never crash the scan."""
        if not self.enabled or not text:
            return False

        body = text
        if len(body) > MAX_MESSAGE_LEN:
            body = body[: MAX_MESSAGE_LEN - 20] + "\n... (truncated)"

        url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": body,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        last_exc: Optional[Exception] = None
        for attempt in range(1, ATTEMPTS + 1):
            try:
                resp = requests.post(url, json=payload, timeout=TIMEOUT)
                if resp.status_code == 200:
                    return True
                logger.warning(
                    "Telegram send failed (attempt %d/%d): HTTP %d %s",
                    attempt, ATTEMPTS, resp.status_code, resp.text[:300],
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("Telegram send error (attempt %d/%d): %s", attempt, ATTEMPTS, exc)
        if last_exc:
            logger.warning("Telegram notification dropped after %d attempts: %s", ATTEMPTS, last_exc)
        return False

    def test(self) -> bool:
        return self.send("Investment tracker: Telegram connectivity check ✅")
