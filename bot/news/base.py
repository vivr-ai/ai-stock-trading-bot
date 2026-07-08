"""Abstract news provider interface and shared Article type."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List


@dataclass
class Article:
    symbol: str
    headline: str
    summary: str
    source: str
    url: str
    published_at: str  # ISO 8601 string


class NewsProvider(ABC):
    """Common interface every news source implements."""

    @abstractmethod
    def fetch(self, symbol: str, lookback_hours: int, limit: int) -> List[Article]:
        """Return recent articles for a single ticker symbol."""
        raise NotImplementedError


def get_news_provider(cfg) -> NewsProvider:
    """Factory: build the provider named in config."""
    from .newsapi_client import NewsAPIProvider
    from .finnhub_client import FinnhubProvider
    from .alpaca_news import AlpacaNewsProvider

    retry_attempts = cfg.retry.max_attempts
    retry_base_delay = cfg.retry.base_delay_seconds

    if cfg.news.provider == "alpaca":
        # Uses your Alpaca account keys (no separate news key needed).
        return AlpacaNewsProvider(
            cfg.alpaca.api_key, cfg.alpaca.secret_key,
            retry_attempts=retry_attempts, retry_base_delay=retry_base_delay,
        )

    if cfg.news.provider == "newsapi":
        if not cfg.news.newsapi_key or cfg.news.newsapi_key.startswith("YOUR_"):
            raise ValueError(
                "news.provider is 'newsapi' but NEWSAPI_API_KEY is not set"
            )
        return NewsAPIProvider(
            cfg.news.newsapi_key, retry_attempts=retry_attempts, retry_base_delay=retry_base_delay,
        )

    if cfg.news.provider == "finnhub":
        if not cfg.news.finnhub_key or cfg.news.finnhub_key.startswith("YOUR_"):
            raise ValueError(
                "news.provider is 'finnhub' but FINNHUB_API_KEY is not set"
            )
        return FinnhubProvider(
            cfg.news.finnhub_key, retry_attempts=retry_attempts, retry_base_delay=retry_base_delay,
        )

    raise ValueError(f"Unknown news provider: {cfg.news.provider}")
