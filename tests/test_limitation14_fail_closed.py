"""Limitation 14 (decision P, 25 Nov 2026): missing, invalid or short-history
market-data inputs must BLOCK new entries - per symbol for that symbol's own
inputs, globally for the SPY market filters - instead of skipping the check.
Valid zero values stay valid. Exits are never gated by these checks."""
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Optional
from zoneinfo import ZoneInfo

import pytest

import bot.trading.alpaca_client as ac
from bot.sentiment.base import SentimentResult
from bot.trading.alpaca_client import AlpacaBroker
from bot.trading.strategy import SentimentStrategy, _valid_number

NAN, INF = float("nan"), float("inf")


@dataclass
class _StrategyCfg:
    max_intraday_runup_pct: float = 8.0
    require_price_above_sma: bool = True
    sma_period: int = 20
    min_volume_ratio: float = 1.5
    volume_lookback_days: int = 20
    min_headlines: int = 5
    sentiment_weight: float = 0.5
    headline_weight: float = 0.2
    volume_weight: float = 0.3
    momentum_buy_score: float = 0.6
    rsi_period: int = 2
    rsi_oversold: float = 10.0
    rsi_exit: float = 65.0
    reversion_trend_sma_period: int = 200
    reversion_min_volume_ratio: float = 1.3
    reversion_sentiment_veto: float = -5.0
    reversion_max_hold_days: int = 5
    market_filter_symbol: str = "SPY"
    market_filter_max_drop_pct: float = 2.0
    market_regime_filter_enabled: bool = True
    market_regime_ma_period: int = 50


@dataclass
class _Cfg:
    strategy: _StrategyCfg = field(default_factory=_StrategyCfg)


@dataclass
class _Snap:
    last: Optional[float]
    change_pct: Optional[float] = None
    sma: Optional[float] = None
    volume_ratio: Optional[float] = None
    trend_sma: Optional[float] = None
    rsi: Optional[float] = None
    history_n: Optional[int] = None


class _Rec:
    def __init__(self):
        self.reasons = []

    def record_decision(self, **kw):
        self.reasons.append(kw.get("reason"))

    def __getattr__(self, _name):
        return lambda *a, **k: None


class _Broker:
    def __init__(self, change):
        self.change, self.calls = change, 0

    def market_change_pct(self, symbol):
        self.calls += 1
        return self.change


class _Fake:
    def __init__(self, change=0.5, cfg=None):
        self.cfg = cfg or _Cfg()
        self.recorder = _Rec()
        self.broker = _Broker(change)
        self._entry_block_reason = None


def _sent(score=10.0, headlines=10):
    return SentimentResult(symbol="T", score=score, label="positive", rationale="t",
                           article_count=headlines)


def good_spy(**kw):
    base = dict(last=760.0, change_pct=0.5, sma=750.0, volume_ratio=1.0, history_n=52)
    base.update(kw)
    return _Snap(**base)


def good_sym(**kw):
    base = dict(last=100.0, change_pct=1.0, sma=95.0, volume_ratio=2.0, history_n=22)
    base.update(kw)
    return _Snap(**base)


def market(fake, spy, allowed=True):
    return SentimentStrategy._apply_market_filters(fake, allowed, spy)


def momentum(snap, fake=None, sentiment=None):
    fake = fake or _Fake()
    return SentimentStrategy._evaluate_momentum_path(fake, "T", sentiment or _sent(), snap), fake


# ---- helpers ------------------------------------------------------------

@pytest.mark.parametrize("x,ok", [(0, True), (0.0, True), (-1.5, True), (3, True), (None, False),
                                  (NAN, False), (INF, False), (-INF, False), ("1.0", False),
                                  (True, False)])
def test_valid_number_distinguishes_zero_from_missing(x, ok):
    assert _valid_number(x) is ok


# ---- global: SPY daily-drop ---------------------------------------------

@pytest.mark.parametrize("change", [None, NAN, INF, -INF])
def test_missing_or_invalid_spy_change_blocks_all_new_entries(change):
    fake = _Fake(change=change)
    assert market(fake, good_spy()) is False
    assert fake.recorder.reasons == ["market_change_unavailable"]
    assert fake._entry_block_reason == "market_change_unavailable"


def test_valid_zero_spy_change_is_not_a_block():
    fake = _Fake(change=0.0)
    assert market(fake, good_spy()) is True
    assert fake.recorder.reasons == []


def test_real_spy_drop_still_blocks_with_its_own_reason():
    fake = _Fake(change=-2.5)
    assert market(fake, good_spy()) is False
    assert fake.recorder.reasons == ["intraday_drop"]


def test_daily_drop_filter_disabled_is_left_alone():
    cfg = _Cfg()
    cfg.strategy.market_filter_max_drop_pct = 0.0
    fake = _Fake(change=None, cfg=cfg)
    assert market(fake, good_spy()) is True
    assert fake.broker.calls == 0


# ---- global: SPY regime -------------------------------------------------

def test_short_regime_history_blocks_all_new_entries():
    fake = _Fake()
    assert market(fake, good_spy(history_n=30)) is False
    assert fake.recorder.reasons == ["regime_history_insufficient"]


def test_exactly_enough_regime_history_is_fine():
    assert market(_Fake(), good_spy(history_n=50)) is True


@pytest.mark.parametrize("spy", [None, "sma_none", "sma_nan", "sma_inf", "sma_zero", "sma_neg",
                                 "last_nan", "last_ninf", "last_zero", "last_neg"])
def test_missing_or_invalid_regime_inputs_block(spy):
    snap = {None: None, "sma_none": good_spy(sma=None), "sma_nan": good_spy(sma=NAN),
            "sma_inf": good_spy(sma=INF), "sma_zero": good_spy(sma=0.0), "sma_neg": good_spy(sma=-5.0),
            "last_nan": good_spy(last=NAN), "last_ninf": good_spy(last=-INF),
            "last_zero": good_spy(last=0.0), "last_neg": good_spy(last=-1.0)}[spy]
    fake = _Fake()
    assert market(fake, snap) is False
    assert fake.recorder.reasons == ["regime_data_unavailable"]


def test_below_regime_sma_still_blocks():
    fake = _Fake()
    assert market(fake, good_spy(last=700.0)) is False
    assert fake.recorder.reasons == ["below_market_regime_sma"]


def test_global_block_clears_when_valid_data_returns():
    fake = _Fake(change=None)
    assert market(fake, good_spy()) is False
    fake2 = _Fake(change=0.4)
    assert market(fake2, good_spy()) is True          # next cycle: nothing latched


def test_entries_already_blocked_skip_the_market_filters():
    fake = _Fake(change=None)
    assert market(fake, None, allowed=False) is False
    assert fake.broker.calls == 0 and fake.recorder.reasons == []


# ---- per symbol: momentum path ------------------------------------------

def test_good_inputs_still_buy():
    reason, _ = momentum(good_sym())
    assert reason is not None


@pytest.mark.parametrize("change", [None, NAN, INF])
def test_missing_or_invalid_symbol_change_blocks_that_entry(change):
    reason, fake = momentum(good_sym(change_pct=change))
    assert reason is None and fake.recorder.reasons == ["change_unavailable"]


def test_valid_zero_symbol_change_can_still_buy():
    reason, fake = momentum(good_sym(change_pct=0.0))
    assert reason is not None and "change_unavailable" not in fake.recorder.reasons


@pytest.mark.parametrize("last", [None, NAN, INF, -INF, 0.0, -1.0])
def test_invalid_price_blocks(last):
    reason, fake = momentum(good_sym(last=last))
    assert reason is None and fake.recorder.reasons == ["change_unavailable"]


def test_short_history_blocks_the_sma_gate():
    reason, fake = momentum(good_sym(history_n=10))
    assert reason is None and fake.recorder.reasons == ["sma_history_insufficient"]


@pytest.mark.parametrize("sma", [None, NAN, INF, -INF, 0.0, -3.0])
def test_missing_or_invalid_sma_blocks(sma):
    reason, fake = momentum(good_sym(sma=sma))
    assert reason is None and fake.recorder.reasons == ["sma_unavailable"]


def test_missing_volume_no_longer_lets_strong_sentiment_through():
    # Before: volume None scored 0 but sentiment 0.5 + headlines 0.2 = 0.7 >= 0.6
    # still bought. Now the missing input blocks.
    reason, fake = momentum(good_sym(volume_ratio=None))
    assert reason is None and fake.recorder.reasons == ["volume_unavailable"]


@pytest.mark.parametrize("vol", [NAN, INF, -0.5])
def test_invalid_volume_blocks(vol):
    reason, fake = momentum(good_sym(volume_ratio=vol))
    assert reason is None and fake.recorder.reasons == ["volume_unavailable"]


def test_short_volume_history_blocks_when_sma_gate_is_off():
    cfg = _Cfg()
    cfg.strategy.require_price_above_sma = False
    reason, fake = momentum(good_sym(history_n=5), fake=_Fake(cfg=cfg))
    assert reason is None and fake.recorder.reasons == ["volume_history_insufficient"]


def test_valid_zero_volume_is_data_not_missing():
    # 0.0x volume is real information: no credit, judged by the score as before.
    reason, fake = momentum(good_sym(volume_ratio=0.0))
    assert "volume_unavailable" not in fake.recorder.reasons
    assert reason is not None                          # 0.5 + 0.2 = 0.7 >= 0.6, unchanged design
    weak, fake2 = momentum(good_sym(volume_ratio=0.0), sentiment=_sent(score=6.0, headlines=5))
    assert weak is None and "volume_unavailable" not in fake2.recorder.reasons


def test_volume_gate_disabled_ignores_missing_volume():
    cfg = _Cfg()
    cfg.strategy.min_volume_ratio = 0.0
    reason, _ = momentum(good_sym(volume_ratio=None), fake=_Fake(cfg=cfg))
    assert reason is not None


def test_symbol_block_clears_when_valid_data_returns():
    assert momentum(good_sym(change_pct=None))[0] is None
    assert momentum(good_sym())[0] is not None


def test_snapshot_without_a_history_count_is_judged_on_its_values_only():
    reason, _ = momentum(good_sym(history_n=None))
    assert reason is not None


# ---- per symbol: reversion path -----------------------------------------

def good_rev(**kw):
    base = dict(last=185.0, sma=180.0, trend_sma=150.0, rsi=5.0, volume_ratio=1.5, history_n=202)
    base.update(kw)
    return _Snap(**base)


def test_reversion_good_inputs_still_fire():
    assert SentimentStrategy._evaluate_reversion_path(_Fake(), "D", _sent(0.0, 1), good_rev())


@pytest.mark.parametrize("kw", [dict(history_n=150), dict(trend_sma=NAN), dict(trend_sma=0.0),
                                dict(trend_sma=-INF), dict(last=0.0), dict(last=NAN), dict(rsi=NAN),
                                dict(rsi=None), dict(volume_ratio=NAN), dict(volume_ratio=None)])
def test_reversion_blocks_on_short_or_invalid_inputs(kw):
    assert SentimentStrategy._evaluate_reversion_path(_Fake(), "D", _sent(0.0, 1), good_rev(**kw)) is None


# ---- exits are not gated ------------------------------------------------

def test_exit_logic_ignores_history_and_entry_checks():
    # A short-history snapshot must not stop an exit from firing.
    snap = _Snap(last=190.0, rsi=70.0, history_n=1)
    assert SentimentStrategy._reversion_exit_reason(_Fake(), snap, {"entry_time": None}) is not None


def test_market_filters_only_change_the_entry_flag(monkeypatch):
    # The method returns a bool and touches only the recorder and the block
    # reason - no broker orders, positions or exit state.
    fake = _Fake(change=None)
    before = dict(vars(fake))
    market(fake, None)
    assert set(vars(fake)) == set(before)


# ---- market_snapshot reports how many completed bars it used ------------

NY = ZoneInfo("America/New_York")


class _Data:
    def __init__(self, bars):
        self.bars = bars

    def get_stock_bars(self, req):
        start = req.start if req.start.tzinfo else req.start.replace(tzinfo=timezone.utc)
        return SimpleNamespace(data={"SPY": [b for b in self.bars if b.timestamp >= start]})

    def get_stock_snapshot(self, req):
        return {}


def _bar(d, c):
    return SimpleNamespace(timestamp=datetime(d.year, d.month, d.day, tzinfo=NY).astimezone(timezone.utc),
                           close=c, volume=1000.0)


def _broker(bars):
    b = AlpacaBroker.__new__(AlpacaBroker)
    b._data = _Data(bars)
    b._retry = lambda fn, label: fn()
    b.latest_price = lambda sym: 100.0
    return b


def test_snapshot_history_count(monkeypatch):
    monkeypatch.setattr(ac, "_utcnow", lambda: datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc))
    few = [_bar(date(2026, 11, d), 100.0 + d) for d in (16, 17, 18, 19, 20)]
    snap = _broker(few).market_snapshot("SPY")
    assert snap.history_n == 5 and snap.sma is not None   # computed, but now flagged as short
    assert _broker([]).market_snapshot("SPY").history_n == 0
