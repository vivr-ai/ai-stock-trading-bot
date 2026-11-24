"""23 Nov 2026 finding: during an open New York session Alpaca's daily-bars
endpoint includes today's still-forming bar. market_snapshot must use
completed sessions only, judged by the NEW YORK session date."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import bot.trading.alpaca_client as ac
from bot.trading.alpaca_client import AlpacaBroker, completed_daily_bars

NY = ZoneInfo("America/New_York")
BNE = ZoneInfo("Australia/Brisbane")
THANKSGIVING = date(2026, 11, 26)
HOLIDAYS = {THANKSGIVING, date(2026, 12, 25), date(2027, 1, 1)}


def bar(d: date, close: float, volume: float = 1000.0):
    # Alpaca stamps daily bars at 00:00 New York time.
    return SimpleNamespace(timestamp=datetime(d.year, d.month, d.day, tzinfo=NY).astimezone(timezone.utc),
                           close=close, volume=volume)


def sessions(last: date, n: int):
    """n trading days ending on `last` (weekdays, Thanksgiving skipped), oldest first."""
    out, d = [], last
    while len(out) < n:
        if d.weekday() < 5 and d not in HOLIDAYS:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def history(last: date, n: int = 70):
    return [bar(d, 100.0 + i, 1000.0 + 10 * i) for i, d in enumerate(sessions(last, n))]


class FakeData:
    def __init__(self, bars):
        self.bars = bars

    def get_stock_bars(self, req):
        start = req.start if req.start.tzinfo else req.start.replace(tzinfo=timezone.utc)
        return SimpleNamespace(data={"SPY": [b for b in self.bars if b.timestamp >= start]})

    def get_stock_snapshot(self, req):
        return {}


def broker(bars, last):
    b = AlpacaBroker.__new__(AlpacaBroker)
    b._data = FakeData(bars)
    b._retry = lambda fn, label: fn()
    b.latest_price = lambda sym: last
    return b


@pytest.fixture
def clock(monkeypatch):
    def set_(dt):
        monkeypatch.setattr(ac, "_utcnow", lambda: dt.astimezone(timezone.utc))
    return set_


def expect(snap, completed, last, sma_n=20, vol_n=20):
    pc = completed[-1].close
    assert snap.prev_close == pytest.approx(pc)
    assert snap.change_pct == pytest.approx((last - pc) / pc * 100)
    assert snap.sma == pytest.approx(sum(b.close for b in completed[-sma_n:]) / sma_n)
    assert snap.avg_volume == pytest.approx(sum(b.volume for b in completed[-vol_n:]) / vol_n)


def test_completed_bars_only_are_used_unchanged(clock):
    clock(datetime(2026, 11, 23, 10, 0, tzinfo=NY))                # Mon 10:00 ET
    bars = history(date(2026, 11, 20))                               # through Fri
    snap = broker(bars, 723.0).market_snapshot("SPY")
    expect(snap, bars, 723.0)


def test_forming_same_day_bar_is_excluded(clock):
    clock(datetime(2026, 11, 23, 10, 0, tzinfo=NY))
    completed = history(date(2026, 11, 20))
    forming = bar(date(2026, 11, 23), 721.18, 5.0)
    snap = broker(completed + [forming], 721.18).market_snapshot("SPY")
    expect(snap, completed, 721.18)
    assert snap.change_pct != pytest.approx(0.0)                     # the daily-drop filter can fire again


def test_forming_bar_excluded_from_50_day_regime_sma(clock):
    clock(datetime(2026, 11, 23, 11, 0, tzinfo=NY))
    completed = history(date(2026, 11, 20), 90)
    snap = broker(completed + [bar(date(2026, 11, 23), 999.0)], 726.0).market_snapshot(
        "SPY", sma_period=50, volume_lookback_days=50)
    expect(snap, completed, 726.0, 50, 50)


def test_weekend_keeps_fridays_bar(clock):
    clock(datetime(2026, 11, 21, 12, 0, tzinfo=NY))                # Saturday
    bars = history(date(2026, 11, 20))
    expect(broker(bars, 690.0).market_snapshot("SPY"), bars, 690.0)


def test_day_after_holiday_uses_last_trading_day(clock):
    clock(datetime(2026, 11, 27, 10, 0, tzinfo=NY))                # Fri after Thanksgiving
    completed = history(date(2026, 11, 25))                          # last completed: Wed
    snap = broker(completed + [bar(date(2026, 11, 27), 500.0)], 700.0).market_snapshot("SPY")
    expect(snap, completed, 700.0)
    assert snap.prev_close == completed[-1].close


def test_brisbane_clock_does_not_change_the_session_date(clock):
    # Tue 01:00 Brisbane = Mon 10:00 New York: Monday is still the open session.
    clock(datetime(2026, 11, 24, 1, 0, tzinfo=BNE))
    completed = history(date(2026, 11, 20))
    snap = broker(completed + [bar(date(2026, 11, 23), 721.0)], 721.0).market_snapshot("SPY")
    expect(snap, completed, 721.0)


def test_new_york_evening_when_utc_is_already_tomorrow(clock):
    # Mon 22:30 New York = Tue 03:30 UTC. The NY session date is Monday, so the
    # Monday bar is excluded (documented: nothing trades then); a UTC date would keep it.
    now = datetime(2026, 11, 23, 22, 30, tzinfo=NY)
    assert now.astimezone(timezone.utc).date() == date(2026, 11, 24)
    clock(now)
    completed = history(date(2026, 11, 20))
    snap = broker(completed + [bar(date(2026, 11, 23), 720.0)], 720.0).market_snapshot("SPY")
    expect(snap, completed, 720.0)


def test_just_after_new_york_midnight_yesterday_is_complete(clock):
    clock(datetime(2026, 11, 24, 0, 30, tzinfo=NY))                # Tue 00:30 ET
    bars = history(date(2026, 11, 23))                               # includes Monday
    expect(broker(bars, 720.0).market_snapshot("SPY"), bars, 720.0)


def test_completed_daily_bars_helper_handles_naive_and_empty():
    now = datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)
    naive_fri = SimpleNamespace(timestamp=datetime(2026, 11, 20, 5, 0), close=1, volume=1)
    naive_mon = SimpleNamespace(timestamp=datetime(2026, 11, 23, 5, 0), close=2, volume=1)
    assert completed_daily_bars([naive_fri, naive_mon], now=now) == [naive_fri]
    assert completed_daily_bars([], now=now) == []
    assert completed_daily_bars(None, now=now) is None


def test_enough_completed_bars_after_a_holiday_cluster(clock, caplog):
    # Mon 4 Jan 2027 10:00 ET: Thanksgiving, Christmas and New Year inside the
    # fetch window, plus today's forming bar dropped. The window must still
    # hold a full 50 completed sessions (no shorter-window SMA).
    clock(datetime(2027, 1, 4, 10, 0, tzinfo=NY))
    completed = history(date(2026, 12, 31), 120)
    caplog.set_level("WARNING")
    snap = broker(completed + [bar(date(2027, 1, 4), 1.0)], 300.0).market_snapshot(
        "SPY", sma_period=50, volume_lookback_days=50)
    expect(snap, completed, 300.0, 50, 50)
    assert "history bar(s)" not in caplog.text


def test_only_a_forming_bar_fails_closed(clock):
    # Nothing completed in the window: no SMA / prev close rather than using the partial bar.
    clock(datetime(2026, 11, 23, 10, 0, tzinfo=NY))
    snap = broker([bar(date(2026, 11, 23), 721.0)], 721.0).market_snapshot("SPY")
    assert snap.sma is None and snap.prev_close is None and snap.change_pct is None


def test_alpaca_limit_semantics_cannot_bring_back_stale_bars(clock):
    # The 21 Nov defect: with start+limit Alpaca keeps the OLDEST N. No limit is
    # sent, so the newest completed session is still the previous close.
    class LimitFake(FakeData):
        def __init__(self, bars):
            super().__init__(bars)
            self.requests = []

        def get_stock_bars(self, req):
            self.requests.append(req)
            rows = super().get_stock_bars(req).data["SPY"]
            if getattr(req, "limit", None):
                rows = rows[: req.limit]
            return SimpleNamespace(data={"SPY": rows})

    clock(datetime(2026, 11, 23, 10, 0, tzinfo=NY))
    completed = history(date(2026, 11, 20), 90)
    b = broker(completed, 720.0)
    b._data = LimitFake(completed + [bar(date(2026, 11, 23), 720.0)])
    snap = b.market_snapshot("SPY", sma_period=50, volume_lookback_days=50)
    assert all(getattr(r, "limit", None) in (None, 0) for r in b._data.requests)
    assert snap.prev_close == completed[-1].close


def test_daily_bars_evidence_line_logged_at_most_twice_per_session(clock, caplog, monkeypatch):
    monkeypatch.setattr(ac, "_DAILY_BARS_LOGGED", set())
    caplog.set_level("INFO", logger="bot.trading.alpaca_client")
    clock(datetime(2026, 11, 23, 9, 30, tzinfo=NY))
    completed = history(date(2026, 11, 20))
    b = broker(completed, 720.0)
    b.market_snapshot("SPY")                                         # 09:30: no forming bar yet
    b.market_snapshot("SPY")                                         # same state: not repeated
    clock(datetime(2026, 11, 23, 10, 0, tzinfo=NY))
    b._data = FakeData(completed + [bar(date(2026, 11, 23), 721.0, 5.0)])
    b.market_snapshot("SPY")                                         # forming bar now dropped
    b.market_snapshot("SPY")
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("DAILY_BARS ")]
    assert len(lines) == 2
    import json
    first, second = (json.loads(l[len("DAILY_BARS "):]) for l in lines)
    assert first["dropped_on_or_after_session"] == []
    assert second["dropped_on_or_after_session"] == [["2026-11-23", 721.0, 5.0]]
    assert second["raw_newest"][0] == "2026-11-23"
    assert second["used"][-1][0] == "2026-11-20"
    assert second["prev_close"] == completed[-1].close
    assert second["used_n"] == len(second["used"])


def test_evidence_logging_failure_never_breaks_the_snapshot(clock, monkeypatch):
    clock(datetime(2026, 11, 23, 10, 0, tzinfo=NY))
    completed = history(date(2026, 11, 20))
    monkeypatch.setattr(ac.logger, "info", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    snap = broker(completed, 720.0).market_snapshot("SPY")
    assert snap.prev_close == completed[-1].close
