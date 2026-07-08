"""Sentiment analysis via the Anthropic (Claude) Messages API.

Docs: https://docs.claude.com/en/api/overview
"""
from __future__ import annotations

import logging
from typing import List

from ..utils.retry import call_with_retry
from ..news.base import Article
from .base import (
    SYSTEM_PROMPT,
    SentimentAnalyzer,
    SentimentResult,
    build_user_prompt,
    parse_sentiment_json,
)

logger = logging.getLogger(__name__)


class ClaudeAnalyzer(SentimentAnalyzer):
    def __init__(self, api_key: str, model: str, temperature: float = 0.0,
                 retry_attempts: int = 4, retry_base_delay: float = 1.0):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._temperature = temperature
        self._retry_attempts = retry_attempts
        self._retry_base_delay = retry_base_delay

    def analyze(self, symbol: str, articles: List[Article]) -> SentimentResult:
        if not articles:
            return SentimentResult(symbol, 0.0, "neutral", "no news", 0)
        try:
            msg = call_with_retry(
                lambda: self._client.messages.create(
                    model=self._model,
                    max_tokens=256,
                    temperature=self._temperature,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": build_user_prompt(symbol, articles)}],
                ),
                attempts=self._retry_attempts, base_delay=self._retry_base_delay,
                op_name=f"claude.messages.create({symbol})",
            )
            text = "".join(
                b.text for b in msg.content if getattr(b, "type", "") == "text"
            )
            return parse_sentiment_json(symbol, text, len(articles))
        except Exception as exc:  # noqa: BLE001 - degrade gracefully, never crash a cycle
            logger.warning("Claude sentiment failed for %s after retries: %s", symbol, exc)
            return SentimentResult(symbol, 0.0, "neutral", f"error: {exc}", len(articles))
