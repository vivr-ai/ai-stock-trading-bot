"""Universe selection: which tickers to evaluate each cycle.

A UniverseProvider returns the candidate list of symbols. The mention-based
provider approximates "top N most mentioned US stocks" by counting ticker
mentions in a broad financial-news feed; the static provider returns a fixed
curated list.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List


class UniverseProvider(ABC):
    @abstractmethod
    def get_universe(self, limit: int) -> List[str]:
        """Return up to `limit` ticker symbols to evaluate this cycle."""
        raise NotImplementedError


def get_universe_provider(cfg) -> UniverseProvider:
    from .static_universe import get_static_universe
    from .mention_counter import MentionUniverse

    if cfg.universe.provider == "static":
        class _Static(UniverseProvider):
            def get_universe(self, limit: int) -> List[str]:
                return get_static_universe(limit)

        return _Static()

    if cfg.universe.provider == "mention":
        if not cfg.news.finnhub_key or cfg.news.finnhub_key.startswith("YOUR_"):
            raise ValueError(
                "universe.provider = 'mention' requires FINNHUB_API_KEY "
                "(it reads Finnhub's general market-news feed)."
            )
        return MentionUniverse(
            finnhub_key=cfg.news.finnhub_key,
            lookback_hours=cfg.news.lookback_hours,
            min_symbols=cfg.universe.min_symbols,
            retry_attempts=cfg.retry.max_attempts,
            retry_base_delay=cfg.retry.base_delay_seconds,
        )

    raise ValueError(f"Unknown universe provider: {cfg.universe.provider}")
