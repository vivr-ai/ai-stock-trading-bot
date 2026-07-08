"""NewsAPI.org provider.

Docs: https://newsapi.org/docs/endpoints/everything
Note: the free NewsAPI tier does not return articles from the most recent
hours and is limited to development use. Finnhub is usually a better fit for
intraday equity news, but this is provided as the requested alternative.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List

import requests

from ..utils.retry import call_with_retry
from .base import Article, NewsProvider

logger = logging.getLogger(__name__)

_BASE_URL = "https://newsapi.org/v2/everything"
# Retry on network/timeout errors and 5xx; NOT on 4xx (bad key, bad params) —
# those won't fix themselves by waiting.
_RETRYABLE = (requests.ConnectionError, requests.Timeout)


class NewsAPIProvider(NewsProvider):
    def __init__(self, api_key: str, timeout: int = 15,
                 retry_attempts: int = 4, retry_base_delay: float = 1.0):
        self._api_key = api_key
        self._timeout = timeout
        self._retry_attempts = retry_attempts
        self._retry_base_delay = retry_base_delay

    def _get(self, params):
        resp = requests.get(_BASE_URL, params=params, timeout=self._timeout)
        if resp.status_code >= 500:
            resp.raise_for_status()  # 5xx: worth retrying
        return resp

    def fetch(self, symbol: str, lookback_hours: int, limit: int) -> List[Article]:
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=lookback_hours)
        params = {
            "q": symbol,
            "from": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "sortBy": "publishedAt",
            "language": "en",
            "pageSize": limit,
            "apiKey": self._api_key,
        }
        try:
            resp = call_with_retry(
                lambda: self._get(params),
                attempts=self._retry_attempts, base_delay=self._retry_base_delay,
                retry_on=_RETRYABLE + (requests.HTTPError,),
                op_name=f"newsapi.fetch({symbol})",
            )
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("NewsAPI fetch failed for %s after retries: %s", symbol, exc)
            return []

        if payload.get("status") != "ok":
            logger.warning("NewsAPI error for %s: %s", symbol, payload.get("message"))
            return []

        articles: List[Article] = []
        for item in payload.get("articles", [])[:limit]:
            source = (item.get("source") or {}).get("name", "newsapi")
            articles.append(
                Article(
                    symbol=symbol,
                    headline=item.get("title", "") or "",
                    summary=item.get("description", "") or "",
                    source=source or "newsapi",
                    url=item.get("url", "") or "",
                    published_at=item.get("publishedAt", now.isoformat()),
                )
            )
        logger.info("NewsAPI: %d articles for %s", len(articles), symbol)
        return articles
