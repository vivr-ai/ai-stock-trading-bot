"""Minimal client for Yahoo Finance's unofficial chart/history endpoint.

Chosen over the trading bot's Alpaca data because it covers ASX, NZX, and
US tickers uniformly with one API shape - Alpaca's data is US-only. This
is an unofficial, undocumented endpoint (no official public Yahoo
portfolio/quote API exists), so it's treated as best-effort: on failure we
log and return None, we never raise into the scan loop, and one bad ticker
never takes down the rest of the scan.

Returns a full year of daily closes (+ volume) so the caller can compute a
50-day moving average, a 52-week high, and a 1-month return all from one
fetch - one network call per symbol, not three.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import List, Optional

import requests

logger = logging.getLogger("investing.yahoo_client")

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
# A real browser UA - Yahoo's unofficial endpoint can 429/999 on the default
# `python-requests/x.y` UA.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 10.0
ATTEMPTS = 3


@dataclass
class PriceHistory:
    symbol: str
    current_price: float
    closes: List[float]  # chronological, most recent last
    volumes: List[float]  # same order, aligned with closes


def fetch_history(symbol: str, *, range_: str = "1y", interval: str = "1d") -> Optional[PriceHistory]:
    """Fetch ~1y of daily closes/volume + the current price for `symbol`.

    Returns None (and logs) on any failure - a missing holding should never
    crash the whole scan, it should just be skipped and reported in the log.
    """
    url = CHART_URL.format(symbol=symbol)
    params = {"range": range_, "interval": interval}

    last_exc: Optional[Exception] = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            payload = resp.json()
            result = (payload.get("chart") or {}).get("result") or []
            if not result:
                err = (payload.get("chart") or {}).get("error")
                raise RuntimeError(f"no 'result' in response (error={err})")

            r0 = result[0]
            meta = r0.get("meta") or {}
            quote = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
            raw_closes = quote.get("close") or []
            raw_volumes = quote.get("volume") or []

            # Drop any (close, volume) pair where close is null (non-trading
            # timestamps / gaps Yahoo sometimes includes), keeping the two
            # lists aligned.
            closes: List[float] = []
            volumes: List[float] = []
            for c, v in zip(raw_closes, raw_volumes):
                if c is None:
                    continue
                closes.append(float(c))
                volumes.append(float(v) if v is not None else 0.0)

            if not closes:
                raise RuntimeError("no usable close prices in response")

            current_price = meta.get("regularMarketPrice")
            if current_price is None:
                current_price = closes[-1]

            return PriceHistory(
                symbol=symbol,
                current_price=float(current_price),
                closes=closes,
                volumes=volumes,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort data source
            last_exc = exc
            if attempt < ATTEMPTS:
                delay = 1.5 * attempt
                logger.warning(
                    "Yahoo fetch for %s failed (attempt %d/%d): %s - retrying in %.1fs",
                    symbol, attempt, ATTEMPTS, exc, delay,
                )
                time.sleep(delay)

    logger.error("Yahoo fetch for %s failed after %d attempts: %s", symbol, ATTEMPTS, last_exc)
    return None
