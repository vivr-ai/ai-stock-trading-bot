"""Thin wrapper around alpaca-py for the operations this bot needs.

Every call that hits the network goes through bot.utils.retry.call_with_retry
(exponential backoff + jitter, configurable attempts). Read-path methods
(is_market_open, latest_price, market_snapshot, etc.) degrade to a safe value
(None / False / empty) when retries are exhausted rather than raising, so one
flaky call slows a cycle but never crashes it. Methods that place or cancel
orders re-raise after exhausting retries, since silently swallowing an order
failure is worse than surfacing it.

Docs: https://alpaca.markets/sdks/python/
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from ..utils.retry import call_with_retry

logger = logging.getLogger(__name__)


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
    avg_volume: Optional[float]
    today_volume: Optional[float]
    volume_ratio: Optional[float]  # today_volume / avg_volume


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

        self._trading = TradingClient(api_key, secret_key, paper=self.connects_to_paper)
        self._data = StockHistoricalDataClient(api_key, secret_key)
        self._retry_attempts = retry_attempts
        self._retry_base_delay = retry_base_delay
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
            return bool(self._retry(self._trading.get_clock, "get_clock").is_open)
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not fetch market clock: %s", exc)
            return False  # fail-safe: treat as closed, do nothing

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
            logger.warning("Could not fetch open orders: %s", exc)
            return set()

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
                        volume_lookback_days: int = 20) -> Optional[MarketSnapshot]:
        """One data pull -> last price, prior-close-based change_pct, N-day
        SMA of close, N-day average volume, and today's volume-so-far.

        change_pct is measured from the PREVIOUS DAY'S CLOSE, not today's
        open, so it captures overnight gaps — exactly the move that news
        creates. SMA/avg-volume are computed over COMPLETED prior days only;
        if today's bar is already forming (mid-session) it is excluded from
        the average and reported separately as `today_volume`, so we're
        comparing today's volume against a clean historical baseline instead
        of leaking today into its own average.

        Note: on Alpaca's free IEX feed, bars reflect IEX-only trades, not
        the full consolidated tape — a reasonable proxy for relative volume,
        not an authoritative print. Fine for this bot's filters; upgrade to a
        SIP subscription if you need exact volume.
        """
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        last = self.latest_price(symbol)
        if last is None:
            return None

        limit = max(sma_period, volume_lookback_days) + 2
        try:
            bars_resp = self._retry(
                lambda: self._data.get_stock_bars(
                    StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day, limit=limit)
                ),
                f"get_stock_bars({symbol})",
            )
            series = bars_resp.data.get(symbol) if hasattr(bars_resp, "data") else bars_resp[symbol]
        except Exception as exc:  # noqa: BLE001
            logger.warning("market_snapshot bars failed for %s: %s", symbol, exc)
            return MarketSnapshot(symbol, last, None, None, None, None, None, None)

        if not series:
            return MarketSnapshot(symbol, last, None, None, None, None, None, None)

        today = datetime.now(timezone.utc).date()

        def _bar_date(bar):
            ts = getattr(bar, "timestamp", None)
            return ts.date() if ts else None

        if _bar_date(series[-1]) == today:
            history = series[:-1]
            today_volume = float(series[-1].volume)
        else:
            history = series
            today_volume = None  # can't confirm today's print yet; fail closed on the volume gate

        prev_close = float(history[-1].close) if history else float(series[-1].open)
        change_pct = (last - prev_close) / prev_close * 100.0 if prev_close > 0 else None

        sma = None
        if len(history) >= 1:
            window = history[-sma_period:]
            sma = sum(float(b.close) for b in window) / len(window)

        avg_volume = None
        if len(history) >= 1:
            window = history[-volume_lookback_days:]
            avg_volume = sum(float(b.volume) for b in window) / len(window)

        volume_ratio = (
            today_volume / avg_volume if today_volume is not None and avg_volume else None
        )

        return MarketSnapshot(
            symbol=symbol, last=last, prev_close=prev_close, change_pct=change_pct,
            sma=sma, avg_volume=avg_volume, today_volume=today_volume, volume_ratio=volume_ratio,
        )

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

    # ---- order placement -----------------------------------------------
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
        from alpaca.trading.requests import (
            MarketOrderRequest,
            StopLossRequest,
            TakeProfitRequest,
        )
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            client_order_id=client_order_id,
            take_profit=TakeProfitRequest(limit_price=round(take_profit_price, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
        )
        order = self._retry(lambda: self._trading.submit_order(req), f"submit_bracket_buy({symbol})")
        return PlacedOrder(
            order_id=str(order.id), symbol=symbol, side="buy", qty=qty,
            stop_price=round(stop_price, 2), take_profit_price=round(take_profit_price, 2),
            status=str(order.status),
        )

    def cancel_orders_for(self, symbol: str) -> None:
        """Cancel any live orders for a symbol (the leftover bracket legs)."""
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        try:
            orders = self._retry(
                lambda: self._trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN)),
                "get_orders(open)",
            )
            for o in orders:
                if o.symbol == symbol:
                    self._retry(lambda: self._trading.cancel_order_by_id(o.id), f"cancel_order({symbol})")
        except Exception as exc:  # noqa: BLE001
            logger.warning("cancel_orders_for %s failed: %s", symbol, exc)

    def close_position(self, symbol: str) -> Optional[PlacedOrder]:
        if not self.allow_submit:
            raise RuntimeError(
                f"close_position({symbol}) blocked: order submission is disabled in "
                f"mode={self.mode}. This should never be reached in normal operation - the "
                "strategy layer checks this first; this is the second line of defense."
            )
        # Cancel the open bracket legs FIRST. Otherwise, after we flatten, a
        # leftover stop or limit leg can still execute and open an unintended
        # short position.
        self.cancel_orders_for(symbol)
        try:
            order = self._retry(lambda: self._trading.close_position(symbol), f"close_position({symbol})")
            return PlacedOrder(
                order_id=str(order.id), symbol=symbol, side="sell",
                qty=int(float(order.qty)) if order.qty else 0,
                stop_price=0.0, take_profit_price=0.0, status=str(order.status),
                filled_avg_price=float(order.filled_avg_price) if getattr(order, "filled_avg_price", None) else None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("close_position failed for %s: %s", symbol, exc)
            return None
