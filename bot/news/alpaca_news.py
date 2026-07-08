"""Alpaca news provider.

Alpaca includes a free news API (Benzinga-sourced) with your account, reachable
through the same alpaca-py SDK and the SAME Alpaca keys you already use for
trading. This is the headline source for "simplified mode": no separate news
API key required.

Docs: https://docs.alpaca.markets/docs/historical-news-data
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List

from ..utils.retry import call_with_retry
from .base import Article, NewsProvider

logger = logging.getLogger(__name__)


class AlpacaNewsProvider(NewsProvider):
    def __init__(self, api_key: str = "", secret_key: str = "",
                 retry_attempts: int = 4, retry_base_delay: float = 1.0):
        from alpaca.data.historical.news import NewsClient

        # NewsClient works with your trading keys; it also works keyless, but
        # passing keys avoids the stricter anonymous rate limits.
        if api_key and secret_key:
            self._client = NewsClient(api_key, secret_key)
        else:
            self._client = NewsClient()
        self._retry_attempts = retry_attempts
        self._retry_base_delay = retry_base_delay

    def fetch(self, symbol: str, lookback_hours: int, limit: int) -> List[Article]:
        from alpaca.data.requests import NewsRequest

        start = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        req = NewsRequest(
            symbols=symbol,
            start=start,
            limit=limit,
            include_content=False,
            exclude_contentless=True,
            sort="desc",
        )
        try:
            result = call_with_retry(
                lambda: self._client.get_news(req),
                attempts=self._retry_attempts, base_delay=self._retry_base_delay,
                op_name=f"alpaca_news.get_news({symbol})",
            )
        except Exception as exc:  # noqa: BLE001 - degrade to no headlines, never crash the cycle
            logger.warning("Alpaca news fetch failed for %s after retries: %s", symbol, exc)
            return []

        # Normalize across SDK return shapes (NewsSet object vs raw dict).
        items = []
        if hasattr(result, "data") and isinstance(getattr(result, "data"), dict):
            items = result.data.get("news", [])
        elif isinstance(result, dict):
            items = result.get("news", [])
        elif hasattr(result, "news"):
            items = result.news

        articles: List[Article] = []
        for it in items[:limit]:
            headline = getattr(it, "headline", None) or (it.get("headline", "") if isinstance(it, dict) else "")
            summary = getattr(it, "summary", None) or (it.get("summary", "") if isinstance(it, dict) else "")
            url = getattr(it, "url", None) or (it.get("url", "") if isinstance(it, dict) else "")
            created = getattr(it, "created_at", None) or (it.get("created_at", "") if isinstance(it, dict) else "")
            articles.append(
                Article(
                    symbol=symbol,
                    headline=headline or "",
                    summary=summary or "",
                    source="alpaca",
                    url=url or "",
                    published_at=str(created),
                )
            )
        logger.info("Alpaca news: %d articles for %s", len(articles), symbol)
        return articles
