"""Approximate "top N most mentioned US stocks" by counting ticker mentions
in Finnhub's general market-news feed.

HONEST LIMITATION: there is no free, authoritative "most mentioned across all
financial news" feed. This counts the `related` ticker tags Finnhub attaches to
articles in its general feed over the lookback window. It's a reasonable proxy,
not ground truth: coverage depends on Finnhub's tagging, and very recent or
thinly-covered names may be missed. When the feed yields fewer than
`min_symbols`, the list is topped up from the bundled static universe so the bot
always has a sensible candidate set. Swap in a dedicated trending-tickers data
source here if you have one.

Docs: https://finnhub.io/docs/api/market-news
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import List

import requests

from ..utils.retry import call_with_retry
from .base import UniverseProvider
from .static_universe import get_static_universe

logger = logging.getLogger(__name__)

_NEWS_URL = "https://finnhub.io/api/v1/news"
# Plausible US equity ticker: 1-5 uppercase letters. Filters out obvious noise.
_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
# Common non-equity tags Finnhub may attach that we don't want to trade.
_BLOCKLIST = {"SPY", "QQQ", "DIA", "IWM", "VIX", "USD", "EUR", "GBP", "BTC", "ETH"}
_RETRYABLE = (requests.ConnectionError, requests.Timeout, requests.HTTPError)


class MentionUniverse(UniverseProvider):
    def __init__(self, finnhub_key: str, lookback_hours: int, min_symbols: int,
                 timeout: int = 15, retry_attempts: int = 4, retry_base_delay: float = 1.0):
        self._key = finnhub_key
        self._lookback_hours = lookback_hours
        self._min_symbols = min_symbols
        self._timeout = timeout
        self._retry_attempts = retry_attempts
        self._retry_base_delay = retry_base_delay

    def get_universe(self, limit: int) -> List[str]:
        counts = self._count_mentions()
        ranked = [sym for sym, _ in counts.most_common() if sym not in _BLOCKLIST]
        selected = ranked[:limit]

        if len(selected) < self._min_symbols:
            logger.info(
                "Mention feed yielded %d tickers (< min_symbols=%d); topping up "
                "from the static universe.",
                len(selected), self._min_symbols,
            )
            for sym in get_static_universe(len(get_static_universe(1000))):
                if sym not in selected:
                    selected.append(sym)
                if len(selected) >= limit:
                    break

        logger.info("Universe (%d symbols): %s", len(selected), ", ".join(selected[:15]) + (" ..." if len(selected) > 15 else ""))
        return selected[:limit]

    def _get(self):
        resp = requests.get(
            _NEWS_URL, params={"category": "general", "token": self._key}, timeout=self._timeout,
        )
        if resp.status_code >= 500:
            resp.raise_for_status()
        return resp

    def _count_mentions(self) -> Counter:
        counter: Counter = Counter()
        try:
            resp = call_with_retry(
                self._get, attempts=self._retry_attempts, base_delay=self._retry_base_delay,
                retry_on=_RETRYABLE, op_name="finnhub.mention_feed",
            )
            resp.raise_for_status()
            articles = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Mention feed fetch failed after retries: %s", exc)
            return counter

        for item in articles:
            related = item.get("related", "") or ""
            for raw in re.split(r"[,\s]+", related):
                sym = raw.strip().upper()
                if _TICKER_RE.match(sym):
                    counter[sym] += 1
        logger.info("Counted mentions across %d articles -> %d distinct tickers",
                    len(articles), len(counter))
        return counter
