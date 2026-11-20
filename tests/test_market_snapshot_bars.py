"""21 Nov finding: market_snapshot asked Alpaca for daily bars with both
`start` and `limit`. Alpaca returns bars oldest-first from `start` and
`limit` keeps the first N, so the "previous close" was days old (on staging,
20 Nov: limit=22 ended 10 Nov). These tests use a fake data client with the
same semantics."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bot.trading.alpaca_client import AlpacaBroker, most_recent_bars


def _bars(n_days=40):
    today = datetime.now(timezone.utc).replace(hour=5, minute=0, second=0, microsecond=0)
    out = []
    for i in range(n_days, 0, -1):                        # oldest first, ending yesterday
        d = today - timedelta(days=i)
        out.append(SimpleNamespace(timestamp=d, close=100.0 + (n_days - i), volume=1000.0 + i))
    return out


class FakeData:
    """Alpaca-like: bars from `start`, oldest first, `limit` keeps the FIRST N."""
    def __init__(self, bars):
        self.bars, self.requests = bars, []

    def get_stock_bars(self, req):
        self.requests.append(req)
        start = req.start if req.start.tzinfo else req.start.replace(tzinfo=timezone.utc)
        rows = [b for b in self.bars if b.timestamp >= start]
        if getattr(req, "limit", None):
            rows = rows[: req.limit]
        return SimpleNamespace(data={"SPY": rows})

    def get_stock_snapshot(self, req):
        return {}


def broker(bars, last):
    b = AlpacaBroker.__new__(AlpacaBroker)
    b._data = FakeData(bars)
    b._retry = lambda fn, label: fn()
    b.latest_price = lambda sym: last
    return b


def test_prev_close_is_the_most_recent_completed_day():
    bars = _bars(60)
    b = broker(bars, last=150.0)
    snap = b.market_snapshot("SPY")
    assert snap.prev_close == pytest.approx(bars[-1].close)          # yesterday, not weeks ago
    assert snap.change_pct == pytest.approx((150.0 - bars[-1].close) / bars[-1].close * 100)
    window = bars[-20:]
    assert snap.sma == pytest.approx(sum(x.close for x in window) / 20)


def test_request_no_longer_sends_limit():
    b = broker(_bars(60), last=150.0)
    b.market_snapshot("SPY", trend_sma_period=50)
    assert all(getattr(r, "limit", None) in (None, 0) for r in b._data.requests)


def test_long_window_trend_sma_uses_latest_bars():
    bars = _bars(120)
    b = broker(bars, last=200.0)
    snap = b.market_snapshot("SPY", trend_sma_period=50)
    assert snap.prev_close == pytest.approx(bars[-1].close)
    assert snap.trend_sma == pytest.approx(sum(x.close for x in bars[-50:]) / 50)


def test_most_recent_bars_orders_and_trims():
    bars = _bars(10)
    shuffled = list(reversed(bars))
    got = most_recent_bars(shuffled, 3)
    assert [g.timestamp for g in got] == [b.timestamp for b in bars[-3:]]
    assert most_recent_bars([], 3) == []
