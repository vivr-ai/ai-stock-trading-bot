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
import re
from typing import Optional

logger = logging.getLogger("bot.notifications.telegram")

TELEGRAM_API_BASE = "https://api.telegram.org"
# Telegram message cap is 4096 UTF-16 code units; stay comfortably under it.
MAX_MESSAGE_LEN = 3800

# The bot token is part of the API URL (/bot<token>/sendMessage), and
# `requests` puts that URL into its exception text. Anything we log about a
# send must go through redact() so the token never reaches the logs.
_TOKEN_IN_URL = re.compile(r"/bot[^/\s'\"]+")
_TOKEN_SHAPE = re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b")


def redact(text, token: str = "") -> str:
    """Remove a Telegram bot token from `text`: the exact token if known,
    any /bot<...> URL segment, and anything shaped like a token."""
    out = str(text)
    if token:
        out = out.replace(token, "<redacted>")
    out = _TOKEN_IN_URL.sub("/bot<redacted>", out)
    return _TOKEN_SHAPE.sub("<redacted>", out)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, *, timeout: float = 8.0,
                 attempts: int = 2, label: str = ""):
        # Optional environment label prefixed to every message (e.g.
        # "[STAGING]") so staging alerts can never be mistaken for production.
        self.label = label
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
        label = f"{self.label} " if self.label else ""
        body = f"{prefix}{label}{text}"
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
                    attempt, self.attempts, resp.status_code,
                    redact(resp.text[:300], self.bot_token),
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("Telegram send error (attempt %d/%d): %s",
                                attempt, self.attempts, redact(exc, self.bot_token))
        if last_exc:
            logger.warning("Telegram notification dropped after %d attempts: %s",
                            self.attempts, redact(last_exc, self.bot_token))
        return False

    def test(self) -> bool:
        """Send a one-off connectivity test message. Used by `main.py --check`
        and can be called manually to verify bot token / chat id are correct."""
        return self.send(
            "Telegram connectivity check from the trading bot ✅", severity="info"
        )
