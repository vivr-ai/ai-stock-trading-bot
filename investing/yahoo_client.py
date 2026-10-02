"""Minimal client for Yahoo Finance's unofficial chart/history endpoint.

Chosen over the trading bot's Alpaca data because it covers ASX, NZX, and
US tickers uniformly with one API shape - Alpaca's data is US-only. This
is an unofficial, undocumented endpoint (no official public Yahoo
portfolio/quote API exists), so it's treated as best-effort: on failure we
log and return None, we never raise into the scan loop, and one bad ticker
never takes down the rest of the scan.

Yahoo has tightened this endpoint over time: an anonymous request with no
cookies/crumb now commonly gets HTTP 429 even at low volume. The fix (the
same one `yfinance` and similar libraries use) is a two-step handshake,
done once per process and reused for every symbol:
  1. Visit a Yahoo page to pick up consent/session cookies.
  2. Fetch a short-lived "crumb" token from /v1/test/getcrumb using those
     cookies, and pass it on every chart request from then on.
A single `requests.Session()` is reused across calls so the cookies persist
between symbols instead of starting fresh (and more likely to be rate
limited) each time.

Returns a full year of daily closes (+ volume) so the caller can compute a
50-day moving average, a 52-week high, and a 1-month return all from one
fetch - one network call per symbol, not three. Also surfaces Yahoo's live
intraday fields (day_low/day_high/current_volume) from the same response,
used by v2's day-low tracking and volume caution flag - no extra call.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import List, Optional

import requests

logger = logging.getLogger("investing.yahoo_client")

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
CONSENT_URL = "https://fc.yahoo.com"
# A real browser UA + Referer - Yahoo's unofficial endpoint is far more
# likely to 429 the default `python-requests/x.y` UA with no Referer.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://finance.yahoo.com/",
}
TIMEOUT = 10.0
ATTEMPTS = 3
# A 429 on the crumb endpoint means the IP itself is being throttled, not
# just this one request - hammering it again immediately only adds to the
# count against us. Cap how often we'll actually attempt a refresh.
CRUMB_REFRESH_COOLDOWN_SECONDS = 60.0

# One session (cookies persist across symbols) and one cached crumb per
# process - refreshed lazily, and no more than once per cooldown window.
_session = requests.Session()
_session.headers.update(HEADERS)
_crumb: Optional[str] = None
_last_crumb_attempt_ts: float = 0.0


@dataclass
class PriceHistory:
    symbol: str
    current_price: float
    closes: List[float]  # chronological, most recent last
    volumes: List[float]  # same order, aligned with closes
    # Live intraday fields from Yahoo's `meta` block - used by v2's day-low
    # tracking and volume caution flag. None if Yahoo didn't include them
    # (e.g. outside market hours) - callers must handle that.
    day_low: Optional[float] = None
    day_high: Optional[float] = None
    current_volume: Optional[float] = None


def _refresh_crumb() -> Optional[str]:
    """Attempt a crumb refresh, but never more than once per cooldown
    window - a 429 here means the *IP* is rate-limited, so retrying this
    call immediately just adds more requests against the same limit
    instead of working around it. Within the cooldown, this is a no-op
    that returns whatever's cached (possibly None)."""
    global _crumb, _last_crumb_attempt_ts

    now = time.monotonic()
    if now - _last_crumb_attempt_ts < CRUMB_REFRESH_COOLDOWN_SECONDS:
        return _crumb
    _last_crumb_attempt_ts = now

    try:
        _session.get(CONSENT_URL, timeout=TIMEOUT)  # best-effort consent cookies
    except Exception as exc:  # noqa: BLE001
        logger.debug("Yahoo consent-cookie fetch failed (continuing anyway): %s", exc)
    try:
        resp = _session.get(CRUMB_URL, timeout=TIMEOUT)
        text = (resp.text or "").strip()
        if resp.status_code == 200 and text and "Too Many Requests" not in text:
            _crumb = text
            return _crumb
        logger.warning(
            "Could not obtain Yahoo crumb (HTTP %d) - likely IP-level throttling; "
            "won't retry this for %.0fs.",
            resp.status_code, CRUMB_REFRESH_COOLDOWN_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Yahoo crumb fetch failed: %s", exc)
    return None


def fetch_history(symbol: str, *, range_: str = "1y", interval: str = "1d") -> Optional[PriceHistory]:
    """Fetch ~1y of daily closes/volume + the current price for `symbol`.

    Returns None (and logs) on any failure - a missing holding should never
    crash the whole scan, it should just be skipped and reported in the log.
    """
    global _crumb
    if _crumb is None:
        _refresh_crumb()

    url = CHART_URL.format(symbol=symbol)

    last_exc: Optional[Exception] = None
    is_rate_limited = False
    for attempt in range(1, ATTEMPTS + 1):
        try:
            params = {"range": range_, "interval": interval}
            if _crumb:
                params["crumb"] = _crumb
            resp = _session.get(url, params=params, timeout=TIMEOUT)

            if resp.status_code == 429:
                # IP-level throttling, not a missing/stale crumb - a fresh
                # crumb attempt is subject to the same cooldown as above
                # (so this is a no-op most of the time, not another request
                # added to the pile). The real fix here is time, handled by
                # the longer backoff below.
                is_rate_limited = True
                _refresh_crumb()
                raise RuntimeError("HTTP 429 (rate limited)")
            if resp.status_code == 401:
                logger.warning("Yahoo fetch for %s got HTTP 401 - refreshing crumb.", symbol)
                _refresh_crumb()
                raise RuntimeError("HTTP 401 (crumb refreshed, will retry)")
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

            def _opt_float(key: str) -> Optional[float]:
                v = meta.get(key)
                return float(v) if v is not None else None

            return PriceHistory(
                symbol=symbol,
                current_price=float(current_price),
                closes=closes,
                volumes=volumes,
                day_low=_opt_float("regularMarketDayLow"),
                day_high=_opt_float("regularMarketDayHigh"),
                current_volume=_opt_float("regularMarketVolume"),
            )
        except Exception as exc:  # noqa: BLE001 - best-effort data source
            last_exc = exc
            if attempt < ATTEMPTS:
                # Rate-limit backoff needs to be much longer than an
                # ordinary transient-error backoff - a couple of seconds
                # does nothing against an IP-level throttle window.
                delay = (15.0 if is_rate_limited else 2.0) * attempt
                logger.warning(
                    "Yahoo fetch for %s failed (attempt %d/%d): %s - retrying in %.1fs",
                    symbol, attempt, ATTEMPTS, exc, delay,
                )
                time.sleep(delay)

    if is_rate_limited:
        logger.error(
            "Yahoo fetch for %s rate-limited after %d attempts - this looks like "
            "IP-level throttling, not a per-symbol issue. Wait a while (30-60+ min) "
            "before retrying rather than re-running immediately.",
            symbol, ATTEMPTS,
        )
    else:
        logger.error("Yahoo fetch for %s failed after %d attempts: %s", symbol, ATTEMPTS, last_exc)
    return None
