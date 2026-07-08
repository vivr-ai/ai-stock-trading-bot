"""Finnhub company-news provider.

Docs: https://finnhub.io/docs/api/company-news
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List

import requests

from ..utils.retry import call_with_retry
from .base import Article, NewsProvider

logger = logging.getLogger(__name__)

_BASE_URL = "https://finnhub.io/api/v1/company-news"
_RETRYABLE = (requests.ConnectionError, requests.Timeout, requests.HTTPError)


class FinnhubProvider(NewsProvider):
    def __init__(self, api_key: str, timeout: int = 15,
                 retry_attempts: int = 4, retry_base_delay: float = 1.0):
        self._api_key = api_key
        self._timeout = timeout
        self._retry_attempts = retry_attempts
        self._retry_base_delay = retry_base_delay

    def _get(self, params):
        resp = requests.get(_BASE_URL, params=params, timeout=self._timeout)
        if resp.status_code >= 500:
            resp.raise_for_status()
        return resp

    def fetch(self, symbol: str, lookback_hours: int, limit: int) -> List[Article]:
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=lookback_hours)
        params = {
            "symbol": symbol,
            "from": start.strftime("%Y-%m-%d"),
            "to": now.strftime("%Y-%m-%d"),
            "token": self._api_key,
        }
        try:
            resp = call_with_retry(
                lambda: self._get(params),
                attempts=self._retry_attempts, base_delay=self._retry_base_delay,
                retry_on=_RETRYABLE,
                op_name=f"finnhub.fetch({symbol})",
            )
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Finnhub fetch failed for %s after retries: %s", symbol, exc)
            return []

        articles: List[Article] = []
        # Finnhub returns newest first; cap to `limit`.
        for item in payload[:limit]:
            ts = item.get("datetime", 0)
            published = (
                datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                if ts
                else now.isoformat()
            )
            articles.append(
                Article(
                    symbol=symbol,
                    headline=item.get("headline", "") or "",
                    summary=item.get("summary", "") or "",
                    source=item.get("source", "finnhub") or "finnhub",
                    url=item.get("url", "") or "",
                    published_at=published,
                )
            )
        logger.info("Finnhub: %d articles for %s", len(articles), symbol)
        return articles
