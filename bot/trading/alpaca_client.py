"""Thin wrapper around alpaca-py for the operations this bot needs.

Every call that hits the network goes through bot.utils.retry.call_with_retry
(exponential backoff + jitter, configurable attempts). Read-path methods
(latest_price, market_snapshot, etc.) degrade to a safe value (None / empty)
when retries are exhausted rather than raising, so one flaky call slows a
cycle but never crashes it. is_market_open is the one exception: instead of
degrading to a hardcoded value, it falls back to a local weekday/hours check
(_local_market_open_fallback) so an Alpaca clock-endpoint outage doesn't get
mistaken for a closed market - see is_market_open's docstring/comments.
Methods that place or cancel orders re-raise after exhausting retries, since
silently swallowing an order failure is worse than surfacing it.

Docs: https://alpaca.markets/sdks/python/
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

from ..utils.retry import call_with_retry

logger = logging.getLogger(__name__)


class BrokerReadError(RuntimeError):
    """A broker read failed: the state is UNKNOWN (not empty)."""


@dataclass
class BrokerSnapshot:
    ok: bool
    data: object
    error: str = ""


WORKING_STATUSES = {"new", "accepted", "pending_new", "held", "partially_filled",
                    "pending_replace", "accepted_for_bidding", "calculated"}
TERMINAL_STATUSES = {"filled", "canceled", "expired", "rejected", "replaced", "done_for_day",
                     "stopped", "suspended"}


def http_status(exc) -> Optional[int]:
    code = getattr(exc, "status_code", None)
    if code is None:
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", None)
    try:
        return int(code) if code is not None else None
    except (TypeError, ValueError):
        return None


def classify_submit_error(exc) -> str:
    """'rejected' = Alpaca answered with a definite 4xx (nothing placed, or
    a duplicate ID); 'uncertain' = timeout, connection error or 5xx (the
    order may or may not exist - look it up, never blindly resubmit)."""
    code = http_status(exc)
    if code is not None and 400 <= code < 500 and code not in (408, 429):
        return "rejected"
    return "uncertain"


def flatten_orders(rows) -> List[dict]:
    """Raw order JSON -> flat list; legs get parent_id. Keeps group_id /
    group_role when Alpaca sends them (None otherwise)."""
    out: List[dict] = []
    for o in rows or []:
        if not isinstance(o, dict):
            continue
        out.append(_order_view(o, None))
        for leg in o.get("legs") or []:
            if isinstance(leg, dict):
                out.append(_order_view(leg, o.get("id")))
    return out


def _f(v) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _order_view(o: dict, parent_id) -> dict:
    return {
        "id": o.get("id"), "client_order_id": o.get("client_order_id") or "",
        "symbol": o.get("symbol"), "side": (o.get("side") or "").lower(),
        "type": (o.get("type") or o.get("order_type") or "").lower(),
        "order_class": (o.get("order_class") or "").lower(),
        "status": (o.get("status") or "").lower(),
        "qty": _f(o.get("qty")), "filled_qty": _f(o.get("filled_qty")),
        "filled_avg_price": _f(o.get("filled_avg_price")) or None,
        "limit_price": _f(o.get("limit_price")) or None, "stop_price": _f(o.get("stop_price")) or None,
        "group_id": o.get("group_id"), "group_role": o.get("group_role"),
        "replaces": o.get("replaces"), "replaced_by": o.get("replaced_by"),
        "parent_id": parent_id, "created_at": o.get("created_at"),
    }


class OrderRejected(RuntimeError):
    """Alpaca gave a definite rejection (4xx): nothing was placed."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class OrderOutcomeUnknown(RuntimeError):
    """The submission's outcome could not be established (response lost and
    the order could not be found by its client ID). Nothing is resubmitted
    automatically; the caller must reconcile from broker state first."""


def _looks_duplicate_id(exc) -> bool:
    text = str(exc).lower()
    return "client_order_id" in text and ("unique" in text or "duplicate" in text or "exists" in text)


class OrderBlockedError(RuntimeError):
    """An order action was refused before reaching Alpaca (e.g. this process
    doesn't hold the trading lease). Nothing was sent to the broker."""

# Standard NYSE/Nasdaq regular-session hours. Deliberately not calendar-aware
# (early closes - e.g. the day after Thanksgiving - aren't accounted for);
# on those rare days this over-estimates remaining session time, which makes
# _session_elapsed_fraction UNDER-estimate how much of the day has elapsed,
# which makes the volume gate MORE conservative than it needs to be that one
# day - consistent with this bot's fail-closed philosophy elsewhere, and far
# simpler than a calendar lookup for a handful of days a year.
_SESSION_OPEN_HOUR, _SESSION_OPEN_MINUTE = 9, 30
_SESSION_CLOSE_HOUR, _SESSION_CLOSE_MINUTE = 16, 0
# Floor on the elapsed fraction: without one, the first minute or two after
# the open would divide by a near-zero expected volume and produce a
# meaningless, huge volume_ratio. 5% of the session (~20 min) is a
# deliberately conservative floor - early trades are volatile and thin
# enough that "confirmed" shouldn't be easy to claim in the first few
# minutes anyway.
_MIN_SESSION_FRACTION = 0.05

# Static NYSE/Nasdaq full-market-holiday list, used ONLY by
# _local_market_open_fallback below (i.e. only when Alpaca's own /v2/clock
# endpoint is unreachable after retries - see is_market_open). Best-effort
# and hand-maintained on purpose, same simplicity trade-off as
# _SESSION_OPEN_HOUR/_SESSION_CLOSE_HOUR above: a missing/wrong entry only
# matters in the narrow window where BOTH this list is stale/wrong AND
# Alpaca's clock endpoint is simultaneously down, which the fallback would
# then misjudge as a normal trading day. That's still strictly better than
# the old behaviour of unconditionally treating every clock-endpoint error as
# "market closed", which silently cost real scan cycles during ordinary
# trading hours (e.g. 2026-09-11: two cycles skipped over a transient Alpaca
# 500 on /v2/clock, even though the market was open both times).
# Update this list once a year; https://www.nyse.com/markets/hours-calendars
_NYSE_HOLIDAYS_ET: Set[date] = {
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # Martin Luther King Jr. Day
    date(2026, 2, 16),   # Washington's Birthday (Presidents' Day)
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed - Jul 4 is a Saturday)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
}


def _local_market_open_fallback(tz_name: str = "America/New_York") -> bool:
    """Best-effort "is it regular trading hours right now" check with no
    network dependency at all - used ONLY as a fallback inside
    is_market_open() when Alpaca's own clock endpoint can't be reached after
    retries. Weekday + 9:30am-4:00pm ET + not in _NYSE_HOLIDAYS_ET; ignores
    early-close days, same as _session_elapsed_fraction above. Deliberately
    NOT the default path - real market data used everywhere else in this
    module still goes through Alpaca; this only exists so one flaky Alpaca
    endpoint doesn't make the bot blind to genuinely open trading hours.
    """
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo(tz_name))
    if now.weekday() >= 5:  # Saturday/Sunday
        return False
    if now.date() in _NYSE_HOLIDAYS_ET:
        return False
    session_open = now.replace(hour=_SESSION_OPEN_HOUR, minute=_SESSION_OPEN_MINUTE,
                               second=0, microsecond=0)
    session_close = now.replace(hour=_SESSION_CLOSE_HOUR, minute=_SESSION_CLOSE_MINUTE,
                                second=0, microsecond=0)
    return session_open <= now <= session_close


def _compute_rsi(closes: List[float], period: int) -> Optional[float]:
    """Wilder's RSI over `closes` (oldest -> newest), for `period` bars.

    Used for the Strategy v2 mean-reversion leg's RSI(2) signal (see
    bot/trading/strategy.py's _evaluate_reversion_path). With only `period`
    changes available (the minimum this function accepts), the Wilder
    smoothing loop below has nothing left to smooth over and this reduces to
    a plain average of gains/losses over that window - the standard way
    short-period RSI (e.g. Connors' RSI(2)) is computed in practice, since
    Wilder's extra smoothing barely matters at such a short period anyway.

    Returns None if there isn't at least `period` + 1 closes (i.e. `period`
    price changes) to work with - fail-closed, same as every other
    confirmation filter in this module.
    """
    if period <= 0 or len(closes) < period + 1:
        return None
    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _session_elapsed_fraction(tz_name: str) -> float:
    """Fraction (0..1) of today's regular trading session elapsed right now.

    Used to scale a full trading day's historical average volume down to
    what's normally expected BY THIS POINT in the day, so today's
    volume-so-far is compared against a fair same-time-of-day baseline
    instead of a full day's total. Comparing partial-day volume to a full
    day's average made the volume-confirmation gate nearly impossible to
    clear before mid-afternoon, regardless of how genuinely elevated a
    stock's volume was - see market_snapshot's docstring.

    Clamped to [_MIN_SESSION_FRACTION, 1.0]: the floor avoids a near-zero
    denominator right after the open; the ceiling means a run well after the
    close (e.g. `main.py --force` outside market hours) just compares
    against the full day's average, same as before this change.
    """
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo(tz_name))
    session_open = now.replace(hour=_SESSION_OPEN_HOUR, minute=_SESSION_OPEN_MINUTE,
                               second=0, microsecond=0)
    session_close = now.replace(hour=_SESSION_CLOSE_HOUR, minute=_SESSION_CLOSE_MINUTE,
                                second=0, microsecond=0)
    session_len = (session_close - session_open).total_seconds()
    elapsed = (now - session_open).total_seconds()
    fraction = elapsed / session_len if session_len > 0 else 1.0
    return max(_MIN_SESSION_FRACTION, min(1.0, fraction))


@dataclass
class PlacedOrder:
    order_id: str
    symbol: str
    side: str
    qty: int
    stop_price: float
    take_profit_price: float
    status: str
    filled_avg_price: Optional[float] = None


@dataclass
class PositionDetail:
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pl: float
    unrealized_plpc: float


@dataclass
class MarketSnapshot:
    symbol: str
    last: float
    prev_close: Optional[float]
    change_pct: Optional[float]
    sma: Optional[float]
    avg_volume: Optional[float]  # full trading day's N-day average - unchanged meaning
    today_volume: Optional[float]
    # today_volume / expected_volume_so_far, NOT today_volume / avg_volume - see
    # _session_elapsed_fraction and market_snapshot's docstring for why.
    volume_ratio: Optional[float]
    # avg_volume scaled down to what's normally expected by THIS POINT in the
    # trading session - the actual denominator behind volume_ratio. Exposed
    # separately (rather than folded silently into volume_ratio) so logs and
    # the dashboard can show both the raw historical average and the
    # time-of-day-adjusted baseline it was compared against.
    expected_volume_so_far: Optional[float] = None
    # --- Strategy v2, Path B (mean-reversion) fields - only populated when
    # market_snapshot() is called with trend_sma_period / rsi_period set;
    # None otherwise (existing callers that don't ask for them are unaffected). ---
    trend_sma: Optional[float] = None   # long-horizon (e.g. 200-day) SMA of close, for the uptrend filter
    rsi: Optional[float] = None         # short-period (e.g. 2-day) Wilder RSI, intraday-updated via `last`


@dataclass
class FillInfo:
    order_id: str
    symbol: str
    side: str
    qty: float
    filled_avg_price: float
    filled_at: Optional[str]


class AlpacaBroker:
    """`mode` picks which Alpaca account this connects to ("paper" -> Alpaca's
    paper endpoint; "dry_run"/"live" -> the real live endpoint, since dry-run
    rehearses against real account data). `allow_submit` is a SEPARATE gate
    controlling whether submit_bracket_buy/close_position are allowed to
    actually place an order - this is the second layer of defense behind
    bot/config.py's validation: even if a caller somehow reached this class
    with mode="live" incorrectly, order methods still refuse unless
    allow_submit is explicitly True. Belt and suspenders, on purpose - this
    class trades real money in "live" mode and that deserves more than one
    guard."""

    def __init__(self, api_key: str, secret_key: str, mode: str = "paper",
                 allow_submit: bool = False,
                 retry_attempts: int = 4, retry_base_delay: float = 1.0):
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient

        if mode not in ("paper", "dry_run", "live"):
            raise ValueError(f"AlpacaBroker: unknown mode '{mode}' (expected paper/dry_run/live)")
        if not api_key or not secret_key:
            raise ValueError(
                "Alpaca API key/secret missing. Set ALPACA_API_KEY/ALPACA_SECRET_KEY (paper) "
                "or ALPACA_LIVE_API_KEY/ALPACA_LIVE_SECRET_KEY (dry_run/live) as appropriate."
            )

        self.mode = mode
        self.connects_to_paper = (mode == "paper")
        # In dry_run mode, submission is never allowed regardless of what's
        # passed in - this is not configurable, on purpose.
        self.allow_submit = bool(allow_submit) and mode != "dry_run"
        # Reliability v3 (2I): optional pre-order check (the trading lease).
        # Callable(kind) -> (ok, reason). None = no extra check (today).
        self.order_guard = None

        self._trading = TradingClient(api_key, secret_key, paper=self.connects_to_paper)
        # v3 6b: alpaca-py's REST client retries 429/504 for EVERY method,
        # including POST /orders, which could create a duplicate order. All
        # reads are already retried by our own call_with_retry wrapper, and
        # order writes go through submit_order_once (exactly one attempt).
        self._trading._retry = 0
        self._trading._retry_codes = []
        # 's' staging / 'p' production: prefix of deterministic order IDs.
        self.env_tag = "p"
        self.last_order_problem: Optional[str] = None
        self._data = StockHistoricalDataClient(api_key, secret_key)
        self._retry_attempts = retry_attempts
        self._retry_base_delay = retry_base_delay
        # Set by is_market_open() on every call: True when the last call had
        # to fall back to _local_market_open_fallback because Alpaca's clock
        # endpoint was unreachable. Callers (see SentimentStrategy.run_cycle)
        # use this to fire a one-time "broker_issue" alert on the transition
        # into/out of degraded mode, rather than trusting logs alone to
        # surface it.
        self.last_clock_degraded: bool = False
        logger.info(
            "AlpacaBroker initialized: mode=%s (%s account), order submission %s",
            mode, "paper" if self.connects_to_paper else "LIVE",
            "ENABLED" if self.allow_submit else "disabled",
        )

    def _retry(self, fn, op_name: str):
        return call_with_retry(
            fn, attempts=self._retry_attempts, base_delay=self._retry_base_delay,
            op_name=op_name,
        )

    # ---- account / market state (retried; degrade to a safe default) -------
    def is_market_open(self) -> bool:
        try:
            is_open = bool(self._retry(self._trading.get_clock, "get_clock").is_open)
            self.last_clock_degraded = False
            return is_open
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not fetch market clock: %s", exc)
            self.last_clock_degraded = True
            # Previously this unconditionally returned False here ("fail-safe:
            # treat as closed, do nothing"), which sounds conservative but
            # actually means a transient Alpaca clock-endpoint outage silently
            # cancels real scan cycles during normal trading hours - see
            # 2026-09-11, where two 30-min cycles were skipped over a
            # passing Alpaca 500 even though the market was open both times.
            # A local weekday/hours check (no network call, so it can't be
            # affected by the same outage) is strictly more accurate than
            # always assuming "closed", so use that instead; it only defers
            # to "closed" itself on weekends/known holidays or outside
            # 9:30-4:00 ET.
            fallback_open = _local_market_open_fallback()
            logger.warning(
                "Alpaca clock unreachable; falling back to local weekday/hours "
                "check (America/New_York, static holiday list) instead of "
                "assuming closed: market_open=%s",
                fallback_open,
            )
            return fallback_open

    def account_identity(self) -> Dict[str, object]:
        """Which Alpaca account these keys belong to (used by
        bot/deployment_guard.py at start-up). Raises on API failure - the
        caller decides whether that is fatal."""
        acct = self._retry(self._trading.get_account, "get_account(identity)")
        return {
            "account_number": str(getattr(acct, "account_number", "") or ""),
            "status": str(getattr(acct, "status", "") or ""),
            "paper": self.connects_to_paper,
        }

    def account_snapshot(self) -> Dict[str, float]:
        """One call returns everything the cycle needs, with retries.

        daytrade_count / pattern_day_trader come straight from Alpaca, which
        is the authoritative source for PDT status (it enforces the rule,
        we're just surfacing it) - see bot/trading/strategy.py's PDT warning
        and the dashboard's Live Readiness page."""
        acct = self._retry(self._trading.get_account, "get_account")
        return {
            "portfolio_value": float(getattr(acct, "portfolio_value", acct.equity)),
            "equity": float(acct.equity),
            "last_equity": float(acct.last_equity),
            "buying_power": float(acct.buying_power),
            "cash": float(getattr(acct, "cash", 0.0) or 0.0),
            "daytrade_count": int(getattr(acct, "daytrade_count", 0) or 0),
            "pattern_day_trader": bool(getattr(acct, "pattern_day_trader", False)),
        }

    def open_positions(self) -> Dict[str, float]:
        positions = self._retry(self._trading.get_all_positions, "get_all_positions")
        return {p.symbol: float(p.qty) for p in positions}

    def open_positions_detailed(self) -> Dict[str, PositionDetail]:
        """Full position detail (entry price, current price, unrealized P/L)
        straight from Alpaca — used by the performance report so we don't
        have to recompute unrealized P/L ourselves."""
        try:
            positions = self._retry(self._trading.get_all_positions, "get_all_positions")
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not fetch detailed positions: %s", exc)
            return {}
        out: Dict[str, PositionDetail] = {}
        for p in positions:
            try:
                out[p.symbol] = PositionDetail(
                    symbol=p.symbol,
                    qty=float(p.qty),
                    avg_entry_price=float(p.avg_entry_price),
                    current_price=float(getattr(p, "current_price", 0.0) or 0.0),
                    market_value=float(getattr(p, "market_value", 0.0) or 0.0),
                    unrealized_pl=float(getattr(p, "unrealized_pl", 0.0) or 0.0),
                    unrealized_plpc=float(getattr(p, "unrealized_plpc", 0.0) or 0.0) * 100.0,
                )
            except (TypeError, ValueError) as exc:
                logger.warning("Skipping malformed position record for %s: %s", p.symbol, exc)
        return out

    def total_exposure(self) -> float:
        total = 0.0
        positions = self._retry(self._trading.get_all_positions, "get_all_positions")
        for p in positions:
            try:
                total += abs(float(p.market_value))
            except (TypeError, ValueError):
                continue
        return total

    def pending_order_symbols(self) -> Set[str]:
        """Symbols with a live (unfilled) order — used to avoid double-buying
        before a prior order has shown up as a position."""
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        try:
            orders = self._retry(
                lambda: self._trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN)),
                "get_orders(open)",
            )
            return {o.symbol for o in orders}
        except Exception as exc:  # noqa: BLE001
            # v3 2H: fail CLOSED. An API error is "unknown", never "no
            # pending orders" - run_cycle treats this as a failed account
            # snapshot and places no orders this cycle (alerted).
            logger.warning("Could not fetch open orders (state UNKNOWN): %s", exc)
            raise BrokerReadError(f"open orders unknown: {exc}") from exc

    def latest_price(self, symbol: str) -> Optional[float]:
        from alpaca.data.requests import StockLatestTradeRequest

        try:
            req = StockLatestTradeRequest(symbol_or_symbols=symbol)
            result = self._retry(
                lambda: self._data.get_stock_latest_trade(req), f"latest_trade({symbol})"
            )
            return float(result[symbol].price)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch price for %s: %s", symbol, exc)
            return None

    def market_snapshot(self, symbol: str, sma_period: int = 20,
                        volume_lookback_days: int = 20,
                        session_tz: str = "America/New_York",
                        trend_sma_period: Optional[int] = None,
                        rsi_period: Optional[int] = None) -> Optional[MarketSnapshot]:
        """One data pull -> last price, prior-close-based change_pct, N-day
        SMA of close, N-day average volume, and today's volume-so-far.

        trend_sma_period / rsi_period are optional additions for the
        Strategy v2 mean-reversion leg (see bot/trading/strategy.py's
        _evaluate_reversion_path / _reversion_exit_reason): when given, this
        also computes a long-horizon SMA (e.g. 200-day, the Connors-style
        uptrend filter) and a short-period RSI (e.g. RSI(2), intraday-updated
        using `last` as the most recent "close" - see _compute_rsi). Both
        stay None when their period isn't passed in, so every existing
        caller is unaffected.

        change_pct is measured from the PREVIOUS DAY'S CLOSE, not today's
        open, so it captures overnight gaps — exactly the move that news
        creates. SMA/avg-volume are computed over COMPLETED prior days
        (get_stock_bars with TimeFrame.Day never returns an in-progress
        "today" bar, confirmed empirically - see today_volume below).
        today's volume-so-far comes from a separate Snapshot call, so we're
        comparing it against a clean historical baseline instead of leaking
        a same-day bar into its own average.

        volume_ratio is time-of-day adjusted: today_volume is compared
        against avg_volume scaled down by how much of the trading session
        has elapsed (see _session_elapsed_fraction), NOT against the raw
        full-day average. Comparing a partial day directly to a full day's
        average made the gate nearly impossible to clear before mid-
        afternoon regardless of how genuinely elevated a stock's volume was
        - confirmed in production logs across multiple days where stocks
        with strong, sustained bullish coverage all day (e.g. sentiment
        score 9-10) still couldn't clear 0.6x by 30 minutes before the
        close. Comparing to the expected volume for THIS point in the day
        instead makes the STRATEGY_MIN_VOLUME_RATIO threshold meaningful at
        any hour, not just near the close.

        Note: on Alpaca's free IEX feed, bars reflect IEX-only trades, not
        the full consolidated tape — a reasonable proxy for relative volume,
        not an authoritative print. Fine for this bot's filters; upgrade to a
        SIP subscription if you need exact volume.
        """
        from alpaca.data.requests import StockBarsRequest, StockSnapshotRequest
        from alpaca.data.timeframe import TimeFrame
        from alpaca.data.enums import DataFeed

        last = self.latest_price(symbol)
        if last is None:
            return None

        limit = max(sma_period, volume_lookback_days, trend_sma_period or 0,
                    (rsi_period or 0) + 1) + 2
        # Alpaca's bars endpoint does NOT infer a historical window from
        # `limit` alone - without an explicit `start` it only returns bars
        # from "now" onward as they form: nothing before the market opens,
        # and exactly one (still-forming) bar for the rest of the day. That
        # silently starved every SMA/volume-average below, which is why the
        # market regime filter and every per-symbol confirmation filter were
        # fail-closing on every single cycle (confirmed via the diagnostic
        # logging this replaced - see git history). ~1.6x calendar-day buffer
        # covers weekends; +5 extra days covers typical holiday clusters.
        start = datetime.now(timezone.utc) - timedelta(days=int(limit * 1.6) + 5)
        try:
            # feed=IEX pinned explicitly on BOTH this call and the snapshot
            # call below. Without it, get_stock_bars silently returns
            # full SIP-consolidated historical volume while the snapshot's
            # real-time daily_bar is IEX-only (all this account is entitled
            # to live) - an apples-to-oranges mismatch that showed up as
            # today's volume ratio reading ~0.01-0.04x on every symbol, every
            # cycle, all day (confirmed in production logs after the previous
            # fix deployed) - i.e. still permanently fail-closed on
            # min_volume_ratio, just via a different, more deceptive number
            # instead of "unavailable". Pinning both to IEX keeps the
            # comparison apples-to-apples on the feed this account actually
            # has real-time access to.
            bars_resp = self._retry(
                lambda: self._data.get_stock_bars(
                    StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day,
                                      start=start, limit=limit, feed=DataFeed.IEX)
                ),
                f"get_stock_bars({symbol})",
            )
            series = bars_resp.data.get(symbol) if hasattr(bars_resp, "data") else bars_resp[symbol]
        except Exception as exc:  # noqa: BLE001
            logger.warning("market_snapshot bars failed for %s: %s", symbol, exc)
            return MarketSnapshot(symbol, last, None, None, None, None, None, None)

        if not series:
            logger.warning("market_snapshot: get_stock_bars(%s, start=%s, limit=%d) "
                           "returned no bars for this symbol.", symbol, start.date(), limit)
            return MarketSnapshot(symbol, last, None, None, None, None, None, None)

        # get_stock_bars(TimeFrame.Day) only ever returns COMPLETED trading
        # days - Alpaca does not backfill an in-progress "today" bar into
        # this endpoint, even mid-session (confirmed empirically: checking
        # the last bar's date against today never matched, at any hour,
        # across multiple trading days - that dead branch used to live
        # here and silently left today_volume=None on every single call,
        # which permanently fail-closed the volume-confirmation gate below
        # for every symbol, every day). So every bar in `series` is history.
        history = series

        if len(history) < sma_period:
            logger.warning(
                "market_snapshot: %s got only %d history bar(s) for a %d-day "
                "SMA (start=%s, limit=%d) - SMA will be computed over a "
                "shorter window than intended.",
                symbol, len(history), sma_period, start.date(), limit,
            )

        # Today's volume-so-far has to come from a different endpoint: the
        # Snapshot API's `daily_bar` is the actual in-progress current-session
        # bar (unlike get_stock_bars above). Best-effort - if this fails, or
        # the market hasn't printed a bar yet (e.g. pre-market), today_volume
        # stays None and the volume gate correctly fails closed rather than
        # guessing, same as before.
        today_volume = None
        try:
            snap_resp = self._retry(
                lambda: self._data.get_stock_snapshot(
                    StockSnapshotRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
                ),
                f"get_stock_snapshot({symbol})",
            )
            snap = snap_resp.get(symbol)
            daily_bar = getattr(snap, "daily_bar", None) if snap else None
            if daily_bar is not None:
                bar_ts = getattr(daily_bar, "timestamp", None)
                if bar_ts is not None and bar_ts.date() == datetime.now(timezone.utc).date():
                    today_volume = float(daily_bar.volume)
        except Exception as exc:  # noqa: BLE001
            logger.warning("market_snapshot: could not fetch today's volume for "
                           "%s via snapshot: %s", symbol, exc)

        prev_close = float(history[-1].close) if history else last
        change_pct = (last - prev_close) / prev_close * 100.0 if prev_close > 0 else None

        sma = None
        if len(history) >= 1:
            window = history[-sma_period:]
            sma = sum(float(b.close) for b in window) / len(window)

        avg_volume = None
        if len(history) >= 1:
            window = history[-volume_lookback_days:]
            avg_volume = sum(float(b.volume) for b in window) / len(window)

        trend_sma = None
        if trend_sma_period and len(history) >= 1:
            window = history[-trend_sma_period:]
            trend_sma = sum(float(b.close) for b in window) / len(window)
            if len(history) < trend_sma_period:
                logger.warning(
                    "market_snapshot: %s got only %d history bar(s) for a %d-day trend "
                    "SMA - computed over a shorter window than intended.",
                    symbol, len(history), trend_sma_period,
                )

        rsi = None
        if rsi_period and len(history) >= rsi_period:
            # Last `rsi_period` completed closes plus today's live price as
            # the most-recent "close" - same intraday-updated treatment
            # `change_pct` above gives `last` vs prev_close.
            closes = [float(b.close) for b in history[-rsi_period:]] + [last]
            rsi = _compute_rsi(closes, rsi_period)

        expected_volume_so_far = (
            avg_volume * _session_elapsed_fraction(session_tz) if avg_volume is not None else None
        )
        volume_ratio = (
            today_volume / expected_volume_so_far
            if today_volume is not None and expected_volume_so_far else None
        )

        return MarketSnapshot(
            symbol=symbol, last=last, prev_close=prev_close, change_pct=change_pct,
            sma=sma, avg_volume=avg_volume, today_volume=today_volume, volume_ratio=volume_ratio,
            expected_volume_so_far=expected_volume_so_far,
            trend_sma=trend_sma, rsi=rsi,
        )

    def max_high_since(self, symbol: str, since: datetime) -> Optional[float]:
        """Highest high of COMPLETED 15-minute regular-session bars (IEX,
        unadjusted prices) since `since`. Used only to rebuild a trailing-stop
        peak when both the database and in-memory copies were lost. None if
        unavailable."""
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        from .trailing import completed_session_high

        try:
            resp = self._retry(
                lambda: self._data.get_stock_bars(StockBarsRequest(
                    symbol_or_symbols=symbol, timeframe=TimeFrame(15, TimeFrameUnit.Minute),
                    start=since, feed=DataFeed.IEX, adjustment=Adjustment.RAW)),
                f"get_stock_bars_15m({symbol})",
            )
            series = resp.data.get(symbol) if hasattr(resp, "data") else resp[symbol]
        except Exception as exc:  # noqa: BLE001
            logger.warning("max_high_since failed for %s: %s", symbol, exc)
            return None
        return completed_session_high(series, datetime.now(timezone.utc))

    def quote(self, symbol: str):
        """Backward-compatible {last, prev_close, change_pct} shape."""
        snap = self.market_snapshot(symbol)
        if snap is None:
            return None
        return {"last": snap.last, "prev_close": snap.prev_close, "change_pct": snap.change_pct}

    def market_change_pct(self, symbol: str) -> Optional[float]:
        """Percent change of a market proxy (e.g. SPY) since prior close."""
        q = self.quote(symbol)
        return q["change_pct"] if q else None

    def portfolio_history(self, period: str = "1M", timeframe: str = "1D"):
        """Equity time series for max-drawdown calculation. Returns None if
        unavailable (older SDK versions / API hiccup) so the report can omit
        drawdown gracefully instead of failing the whole report."""
        try:
            from alpaca.trading.requests import GetPortfolioHistoryRequest

            req = GetPortfolioHistoryRequest(period=period, timeframe=timeframe)
            hist = self._retry(
                lambda: self._trading.get_portfolio_history(req), "get_portfolio_history"
            )
            equity = [float(v) for v in (getattr(hist, "equity", None) or []) if v is not None]
            return equity
        except Exception as exc:  # noqa: BLE001
            logger.warning("portfolio_history unavailable: %s", exc)
            return None

    def last_fill(self, symbol: str, side: Optional[str] = None) -> Optional[FillInfo]:
        """Most recent CLOSED order for a symbol, with its fill price — used
        to price a realized-P/L ledger entry when a bracket stop/take-profit
        fires at the broker (i.e. we didn't place the closing order ourselves
        this cycle, so we have no order id to look up directly)."""
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        try:
            orders = self._retry(
                lambda: self._trading.get_orders(
                    GetOrdersRequest(status=QueryOrderStatus.CLOSED, symbols=[symbol], limit=10)
                ),
                f"get_orders(closed,{symbol})",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("last_fill lookup failed for %s: %s", symbol, exc)
            return None

        for o in orders:
            if side and str(getattr(o, "side", "")).lower() != side.lower():
                continue
            price = getattr(o, "filled_avg_price", None)
            qty = getattr(o, "filled_qty", None)
            if price is None or qty in (None, "0"):
                continue
            return FillInfo(
                order_id=str(o.id), symbol=symbol, side=str(getattr(o, "side", "")),
                qty=float(qty), filled_avg_price=float(price),
                filled_at=str(getattr(o, "filled_at", "") or ""),
            )
        return None

    def get_account_activities(self, after: Optional[str] = None,
                                activity_types: Optional[List[str]] = None) -> List[Dict]:
        """Dividends / regulatory fees / non-resident withholding, straight
        from Alpaca's account activities endpoint. Only meaningful against a
        real account (dry_run/live) - paper accounts don't generate these.

        `after` is an ISO date string ('YYYY-MM-DD'); defaults to activity
        types DIV (dividends), DIVNRA (non-resident-alien dividend
        withholding - the one directly relevant to AU tax residents), and
        FEE (regulatory fees). Degrades to an empty list on any failure
        (unsupported SDK version, network hiccup, etc.) rather than raising,
        matching every other read-path method in this class - the caller
        (main.py's EOD job) treats this as best-effort, not required for a
        trading cycle to succeed.
        """
        from alpaca.trading.requests import GetAccountActivitiesRequest

        types = activity_types or ["DIV", "DIVNRA", "FEE"]
        out: List[Dict] = []
        try:
            req = GetAccountActivitiesRequest(
                activity_types=types, after=after, page_size=100,
            )
            activities = self._retry(
                lambda: self._trading.get_account_activities(req), "get_account_activities"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_account_activities failed (skipping this sync): %s", exc)
            return out

        for a in activities or []:
            try:
                activity_date = getattr(a, "date", None) or getattr(a, "activity_date", None)
                activity_date_str = str(activity_date)[:10] if activity_date else None
                out.append({
                    "activity_id": str(getattr(a, "id", "")),
                    "activity_type": str(getattr(a, "activity_type", "")),
                    "activity_date": activity_date_str,
                    "symbol": getattr(a, "symbol", None),
                    "net_amount": float(getattr(a, "net_amount", 0.0) or 0.0)
                        if getattr(a, "net_amount", None) is not None else None,
                    "qty": float(getattr(a, "qty", 0.0) or 0.0)
                        if getattr(a, "qty", None) is not None else None,
                    "per_share_amount": float(getattr(a, "per_share_amount", 0.0) or 0.0)
                        if getattr(a, "per_share_amount", None) is not None else None,
                    "description": getattr(a, "description", None),
                    "raw": {k: str(v) for k, v in getattr(a, "__dict__", {}).items()},
                })
            except (TypeError, ValueError) as exc:
                logger.warning("Skipping malformed account activity: %s", exc)
        return out

    # ---- Reliability v3 (2H): raw, three-state broker reads -------------
    # Alpaca added fields (e.g. group_id / group_role, Oct 2026) that the
    # installed alpaca-py doesn't model, so these read the raw JSON. Every
    # read returns a BrokerSnapshot: ok=False means UNKNOWN - never "empty".
    RAW_TIMEOUT_S = 10.0

    def _raw(self, method: str, path: str, data=None, retry: bool = True):
        """One raw HTTP call to the Trading API (with a timeout). GETs are
        retried with the broker's backoff; writes (POST/PATCH/DELETE) are
        attempted exactly once - the caller resolves uncertain outcomes by
        looking the order up, never by blind resubmission."""
        t = self._trading
        url = t._base_url + "/" + t._api_version + path
        opts = {"headers": t._get_default_headers(), "allow_redirects": False,
                "timeout": self.RAW_TIMEOUT_S}
        if method.upper() in ("GET", "DELETE"):
            opts["params"] = data
        else:
            opts["json"] = data
        call = lambda: t._one_request(method, url, opts, 0)  # noqa: E731
        if retry and method.upper() == "GET":
            return self._retry(call, f"{method} {path}")
        return call()

    def open_orders_snapshot(self) -> "BrokerSnapshot":
        """All open orders (bracket/OCO legs flattened, each with its
        parent_id), up to 500. ok=False if the read failed."""
        try:
            rows = self._raw("GET", "/orders", {"status": "open", "limit": 500, "nested": "true"})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Open-orders read failed (state UNKNOWN): %s", exc)
            return BrokerSnapshot(False, None, str(exc))
        return BrokerSnapshot(True, flatten_orders(rows or []), "")

    def positions_snapshot(self) -> "BrokerSnapshot":
        """{symbol: {qty, qty_available, avg_entry_price, current_price, side}}."""
        try:
            rows = self._raw("GET", "/positions")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Positions read failed (state UNKNOWN): %s", exc)
            return BrokerSnapshot(False, None, str(exc))
        out = {}
        for p in rows or []:
            try:
                out[p["symbol"]] = {
                    "qty": float(p.get("qty") or 0), "side": p.get("side"),
                    "qty_available": float(p.get("qty_available") if p.get("qty_available") is not None
                                           else p.get("qty") or 0),
                    "avg_entry_price": float(p.get("avg_entry_price") or 0),
                    "current_price": float(p["current_price"]) if p.get("current_price") else None,
                }
            except (KeyError, TypeError, ValueError) as exc:
                return BrokerSnapshot(False, None, f"malformed position row: {exc}")
        return BrokerSnapshot(True, out, "")

    def symbol_orders_snapshot(self, symbol: str, after_iso: Optional[str] = None) -> "BrokerSnapshot":
        """Every order (any status) for one symbol, newest first, legs
        flattened. Used to count earlier attempts of a deterministic ID."""
        params = {"status": "all", "symbols": symbol, "limit": 500, "nested": "true",
                  "direction": "desc"}
        if after_iso:
            params["after"] = after_iso
        rows: list = []
        try:
            for _page in range(self.HISTORY_MAX_PAGES):
                page = self._raw("GET", "/orders", params) or []
                rows.extend(page)
                if len(page) < params["limit"]:
                    return BrokerSnapshot(True, flatten_orders(rows), "")
                # Next (older) page: everything submitted before the oldest row so far.
                params = dict(params, until=min(o.get("submitted_at") or o.get("created_at") for o in page))
        except Exception as exc:  # noqa: BLE001
            return BrokerSnapshot(False, None, str(exc))
        # Too much history to be sure it's complete: callers must treat as UNKNOWN.
        return BrokerSnapshot(False, None, f"order history for {symbol} exceeds "
                                           f"{self.HISTORY_MAX_PAGES} pages")

    HISTORY_MAX_PAGES = 6

    def order_by_client_id(self, client_order_id: str) -> "BrokerSnapshot":
        """ok=True + data=order dict (found), ok=True + data=None (Alpaca says
        404 not found), ok=False (couldn't tell)."""
        try:
            row = self._raw("GET", "/orders:by_client_order_id", {"client_order_id": client_order_id})
            return BrokerSnapshot(True, row, "")
        except Exception as exc:  # noqa: BLE001
            if http_status(exc) == 404:
                return BrokerSnapshot(True, None, "not found")
            return BrokerSnapshot(False, None, str(exc))

    def submit_oco_once(self, symbol: str, qty: float, stop_price: float, take_profit_price: float,
                        client_order_id: str) -> dict:
        """Protective stop + take-profit OCO, ONE attempt, no automatic
        retry. Raises on any failure; use classify_submit_error() and the
        uncertain-submission protocol to decide what happened."""
        if not self.allow_submit:
            raise RuntimeError(f"submit_oco_once({symbol}) blocked: submission disabled ({self.mode})")
        self._guard("protect", f"submit_oco_once({symbol})")
        body = {
            "symbol": symbol, "qty": str(qty), "side": "sell", "type": "limit",
            "time_in_force": "gtc", "order_class": "oco",
            "limit_price": str(round(take_profit_price, 2)),
            "take_profit": {"limit_price": str(round(take_profit_price, 2))},
            "stop_loss": {"stop_price": str(round(stop_price, 2))},
            "client_order_id": client_order_id,
        }
        # Raw single attempt; the protection manager runs its own
        # uncertain-submission protocol across sweeps (no blocking lookups).
        return self._raw("POST", "/orders", body, retry=False)

    # ---- Stage 6b: replace / cancel / quote, single attempt -------------------
    def replace_order_once(self, order_id: str, limit_price: float, client_order_id: str,
                           what: str) -> dict:
        """PATCH one order's limit price, ONE attempt (6a: Alpaca answers 200
        'new' and the old order becomes 'replaced'; a replace on an already
        replaced ID is 422 'order already replaced'; qty can't be changed on
        advanced orders). The response is never proof: the caller re-reads.
          * definite rejection -> OrderRejected
          * ambiguous          -> lookup by the new client ID; found -> that
                                  order; else OrderOutcomeUnknown"""
        if not self.allow_submit:
            raise RuntimeError(f"{what} blocked: order submission is disabled in mode={self.mode}")
        self._guard("exit", what)
        body = {"limit_price": str(round(limit_price, 2)), "client_order_id": client_order_id}
        try:
            return self._raw("PATCH", f"/orders/{order_id}", body, retry=False)
        except Exception as exc:  # noqa: BLE001
            if classify_submit_error(exc) == "rejected":
                raise OrderRejected(f"{what} rejected by Alpaca: {exc}", http_status(exc)) from exc
            return self._resolve_ambiguous(client_order_id, what, exc)

    def cancel_order_once(self, order_id: str, what: str) -> bool:
        """DELETE one order, one attempt. True = Alpaca accepted the cancel
        request (not proof it's cancelled: the caller confirms by re-reading).
        404/422 (already final) -> False. Anything else raises."""
        self._guard("exit", what)
        try:
            self._raw("DELETE", f"/orders/{order_id}", retry=False)
            return True
        except Exception as exc:  # noqa: BLE001
            if http_status(exc) in (404, 422):
                return False
            raise

    def latest_bid(self, symbol: str) -> Optional[float]:
        from alpaca.data.requests import StockLatestQuoteRequest

        try:
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            q = self._retry(lambda: self._data.get_stock_latest_quote(req), f"latest_quote({symbol})")
            bid = float(q[symbol].bid_price or 0)
            return bid if bid > 0 else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch bid for %s: %s", symbol, exc)
            return None

    # ---- order placement -----------------------------------------------
    LOOKUP_DELAYS_S = (1.0, 2.0, 4.0, 8.0)

    def submit_order_once(self, body: dict, kind: str, what: str) -> dict:
        """THE way orders are created (v3 6b). Exactly one POST; never an
        automatic resubmission.
          * accepted            -> the order dict (still not proof it's working)
          * definite rejection  -> OrderRejected (nothing placed). A duplicate
                                   client-ID rejection is resolved by looking
                                   the existing order up and returning it.
          * ambiguous (timeout, connection error, 5xx)
                                -> look the client ID up at 1, 2, 4, 8 s; found
                                   -> that order; otherwise OrderOutcomeUnknown
        """
        if not self.allow_submit:
            raise RuntimeError(f"{what} blocked: order submission is disabled in mode={self.mode}")
        self._guard(kind, what)
        cid = body.get("client_order_id")
        try:
            return self._raw("POST", "/orders", body, retry=False)
        except Exception as exc:  # noqa: BLE001
            if classify_submit_error(exc) == "rejected":
                if cid and _looks_duplicate_id(exc):
                    found = self.order_by_client_id(cid)
                    if found.ok and found.data:
                        logger.warning("%s: client ID %s already exists at Alpaca; using that order.",
                                       what, cid)
                        return found.data
                raise OrderRejected(f"{what} rejected by Alpaca: {exc}", http_status(exc)) from exc
            return self._resolve_ambiguous(cid, what, exc)

    def _resolve_ambiguous(self, cid: Optional[str], what: str, exc) -> dict:
        logger.error("%s: outcome AMBIGUOUS (%s); resolving by client-ID lookup.", what, exc)
        if not cid:
            raise OrderOutcomeUnknown(f"{what}: response lost ({exc}) and no client ID to look up")
        last = "not found"
        for delay in self.LOOKUP_DELAYS_S:
            time.sleep(delay)
            snap = self.order_by_client_id(cid)
            if snap.ok and snap.data:
                logger.warning("%s: order %s found after the ambiguous response (status %s).",
                               what, cid, snap.data.get("status"))
                return snap.data
            last = "not found" if snap.ok else f"lookup failed: {snap.error}"
        raise OrderOutcomeUnknown(
            f"{what}: response lost ({exc}); order {cid} {last} after 15 s. NOT resubmitted - "
            "the next attempt must reconcile broker orders and positions first.")

    def _next_exit_client_id(self, kind: str, symbol: str, position: dict) -> Optional[str]:
        """Deterministic <env><kind>-SYMBOL-<ref>-<n> from broker history, or
        None if the history can't be read or an earlier attempt is still
        working (never stack a second order on an unfinished one)."""
        from .order_ids import attempts_at_broker, position_ref, prefix_for

        fill = self.last_fill(symbol, side="buy")
        prefix = prefix_for(getattr(self, "env_tag", "p"), kind, symbol,
                            position_ref(getattr(fill, "order_id", None), position))
        hist = self.symbol_orders_snapshot(symbol)
        if not hist.ok:
            self.last_order_problem = f"order history unreadable ({hist.error})"
            return None
        used, working = attempts_at_broker(hist.data, prefix)
        if working:
            self.last_order_problem = f"an earlier {kind} order for {symbol} is still working"
            return None
        return prefix + str(used + 1)

    def _guard(self, kind: str, what: str) -> None:
        """Raise OrderBlockedError if the trading lease (or another guard)
        says this process must not touch orders right now."""
        guard = getattr(self, "order_guard", None)
        if guard is None:
            return
        ok, why = guard(kind)
        if not ok:
            logger.error("%s blocked: %s", what, why)
            raise OrderBlockedError(f"{what} blocked: {why}")

    def submit_bracket_buy(self, symbol: str, qty: int, stop_price: float,
                           take_profit_price: float, client_order_id: str = None) -> PlacedOrder:
        """Market BUY with attached stop-loss (-10%) and take-profit (+20%) legs.

        Time-in-force is GTC so the protective legs survive overnight — a DAY
        bracket would expire at the close and leave the position unprotected the
        next morning.

        client_order_id makes the request idempotent: if the process crashes or
        two copies run, Alpaca rejects a second order with the same id instead of
        opening a duplicate position.

        Retries are NOT silent here: if every attempt fails, the exception
        propagates so the caller logs it as a failed buy rather than assuming
        success.
        """
        if not self.allow_submit:
            raise RuntimeError(
                f"submit_bracket_buy({symbol}) blocked: order submission is disabled in "
                f"mode={self.mode}. This should never be reached in normal operation - the "
                "strategy layer checks this first; this is the second line of defense."
            )
        body = {
            "symbol": symbol, "qty": str(qty), "side": "buy", "type": "market",
            "time_in_force": "gtc", "order_class": "bracket",
            "take_profit": {"limit_price": str(round(take_profit_price, 2))},
            "stop_loss": {"stop_price": str(round(stop_price, 2))},
        }
        if client_order_id:
            body["client_order_id"] = client_order_id
        order = self.submit_order_once(body, "entry", f"submit_bracket_buy({symbol})")
        return PlacedOrder(
            order_id=str(order.get("id")), symbol=symbol, side="buy", qty=qty,
            stop_price=round(stop_price, 2), take_profit_price=round(take_profit_price, 2),
            status=str(order.get("status")),
        )

    def get_protected_symbols(self) -> Optional[Set[str]]:
        """Symbols with a live BRACKET or OCO order working at the broker -
        i.e. already have a stop-loss/take-profit protecting them. A normal
        bot-initiated buy always shows up here (submit_bracket_buy always
        attaches one) - this only matters for positions that exist at the
        broker but weren't bought through this bot (manually opened, or
        predate this codebase's trade tracking), which get no bracket by
        default. Used by SentimentStrategy._adopt_legacy_positions to decide
        whether such a position still needs a protective order placed.

        Returns None (not an empty set) when the broker can't be queried, so
        callers can tell "no protected positions" from "couldn't check"."""
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus, OrderSide, OrderClass

        try:
            orders = self._retry(
                lambda: self._trading.get_orders(
                    GetOrdersRequest(status=QueryOrderStatus.OPEN, side=OrderSide.SELL)
                ),
                "get_orders(open, sell)",
            )
            return {
                o.symbol for o in orders
                if getattr(o, "order_class", None) in (OrderClass.OCO, OrderClass.BRACKET)
            }
        except Exception as exc:  # noqa: BLE001
            # Fail CLOSED: an API error means "unknown", not "nothing is
            # protected". Returning an empty set here used to make
            # _adopt_legacy_positions treat every held position as
            # unprotected and submit a second protective order for each.
            logger.warning("Could not fetch protective-order status: %s", exc)
            return None

    def submit_protective_exit(self, symbol: str, qty: float, stop_price: float,
                               take_profit_price: float, client_order_id: str = None) -> PlacedOrder:
        """Attach a stop-loss + take-profit OCO SELL to shares already held
        that the bot did NOT buy itself (see SentimentStrategy.
        _adopt_legacy_positions) - the same protection submit_bracket_buy
        gives a bot-initiated entry, just issued against an existing
        position instead of as part of the buy order. OCO here means two
        linked SELL legs (a limit at take-profit, a stop at stop-loss);
        whichever fills first cancels the other - functionally equivalent
        to a BRACKET's exit legs without needing a new entry order."""
        if not self.allow_submit:
            raise RuntimeError(
                f"submit_protective_exit({symbol}) blocked: order submission is disabled in "
                f"mode={self.mode}. This should never be reached in normal operation - the "
                "strategy layer checks this first; this is the second line of defense."
            )
        body = {
            "symbol": symbol, "qty": str(qty), "side": "sell", "type": "limit",
            "time_in_force": "gtc", "order_class": "oco",
            "limit_price": str(round(take_profit_price, 2)),
            "take_profit": {"limit_price": str(round(take_profit_price, 2))},
            "stop_loss": {"stop_price": str(round(stop_price, 2))},
        }
        if client_order_id:
            body["client_order_id"] = client_order_id
        order = self.submit_order_once(body, "protect", f"submit_protective_exit({symbol})")
        return PlacedOrder(
            order_id=str(order.get("id")), symbol=symbol, side="sell", qty=qty,
            stop_price=round(stop_price, 2), take_profit_price=round(take_profit_price, 2),
            status=str(order.get("status")),
        )

    def cancel_orders_for(self, symbol: str, settle_timeout_s: float = 20.0,
                          poll_interval_s: float = 0.5) -> None:
        """Cancel any live orders for a symbol (the leftover bracket legs),
        then wait (up to settle_timeout_s) until Alpaca no longer reports any
        open order for it. Alpaca processes cancels asynchronously - an order
        sits in pending_cancel for a moment and its shares stay reserved
        (held_for_orders) until the cancel completes - so calling
        close_position immediately afterwards can fail with "insufficient
        qty available". Waiting here is what lets close_position safely
        flatten a position that has a standing protective order."""
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        def _open_for_symbol():
            orders = self._retry(
                lambda: self._trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN)),
                "get_orders(open)",
            )
            return [o for o in orders if o.symbol == symbol]

        try:
            self._guard("cancel", f"cancel_orders_for({symbol})")
        except OrderBlockedError:
            return
        try:
            open_orders = _open_for_symbol()
            for o in open_orders:
                self._retry(lambda o=o: self._trading.cancel_order_by_id(o.id),
                            f"cancel_order({symbol})")
            if not open_orders:
                return
            deadline = time.monotonic() + settle_timeout_s
            while time.monotonic() < deadline:
                time.sleep(poll_interval_s)
                if not _open_for_symbol():
                    return
            logger.warning(
                "cancel_orders_for %s: orders still open after %.1fs; attempting close anyway",
                symbol, settle_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("cancel_orders_for %s failed: %s", symbol, exc)

    def close_position(self, symbol: str) -> Optional[PlacedOrder]:
        if not self.allow_submit:
            raise RuntimeError(
                f"close_position({symbol}) blocked: order submission is disabled in "
                f"mode={self.mode}. This should never be reached in normal operation - the "
                "strategy layer checks this first; this is the second line of defense."
            )
        try:
            self._guard("exit", f"close_position({symbol})")
        except OrderBlockedError:
            return None   # caller treats as a failed sell; nothing was cancelled
        self.last_order_problem = None
        # Cancel the open bracket legs FIRST. Otherwise, after we flatten, a
        # leftover stop or limit leg can still execute and open an unintended
        # short position.
        self.cancel_orders_for(symbol)
        # v3 6b: confirm the actual position from a fresh read, then sell it
        # with ONE deterministic order (<env>X-SYMBOL-<ref>-<n>) instead of
        # DELETE /positions (which can't carry a client ID, so a lost
        # response could never be resolved).
        pos = self.positions_snapshot()
        if not pos.ok:
            self.last_order_problem = f"position unknown ({pos.error}); no sell submitted"
            logger.error("close_position %s: %s", symbol, self.last_order_problem)
            return None
        p = (pos.data or {}).get(symbol)
        if not p or (p.get("qty") or 0) <= 0:
            self.last_order_problem = "no long position at the broker"
            return None
        qty = p["qty"]
        cid = self._next_exit_client_id("X", symbol, p)
        if cid is None:
            logger.error("close_position %s: %s; no sell submitted", symbol, self.last_order_problem)
            return None
        body = {"symbol": symbol, "qty": str(qty), "side": "sell", "type": "market",
                "time_in_force": "day", "client_order_id": cid}
        # Shares stay "held_for_orders" until the leg cancels settle, so the
        # sell can be REJECTED with "insufficient qty available" for a few
        # seconds (seen in production, AMZN 14 Oct). A definite rejection
        # means nothing was placed, so the same order is retried (up to 5
        # tries over ~24 s). An ambiguous outcome is never retried.
        delays = [2.0, 4.0, 8.0, 10.0]
        while True:
            try:
                order = self.submit_order_once(body, "exit", f"close_position({symbol})")
            except OrderBlockedError as exc:
                self.last_order_problem = str(exc)
                return None
            except OrderOutcomeUnknown as exc:
                self.last_order_problem = f"OUTCOME UNKNOWN: {exc}"
                logger.error("close_position %s: %s", symbol, exc)
                return None
            except OrderRejected as exc:
                if "insufficient qty" in str(exc).lower() and delays:
                    time.sleep(delays.pop(0))
                    continue
                self.last_order_problem = str(exc)
                logger.warning("close_position failed for %s: %s", symbol, exc)
                return None
            return PlacedOrder(
                order_id=str(order.get("id")), symbol=symbol, side="sell", qty=qty,
                stop_price=0.0, take_profit_price=0.0, status=str(order.get("status")),
                filled_avg_price=float(order["filled_avg_price"]) if order.get("filled_avg_price") else None,
            )
