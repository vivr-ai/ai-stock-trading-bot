"""Strategy v2: Path A (weighted composite sentiment-momentum score) and
Path B (RSI-2 mean-reversion). Exercises SentimentStrategy's decision
methods directly against minimal fakes (same pattern as test_risk.py's
_RiskCfg) - no broker/DB/network required, and no SentimentStrategy(...)
construction (that pulls in strategy_version/bot_control), just its
_evaluate_momentum_path / _evaluate_reversion_path / _reversion_exit_reason
methods called unbound against a lightweight fake `self`.
"""
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.sentiment.base import SentimentResult
from bot.trading.strategy import SentimentStrategy


@dataclass
class _StrategyCfg:
    buy_threshold: float = 8.0
    sell_threshold: float = -5.0
    min_headlines: int = 5
    sell_min_headlines: int = 3
    max_intraday_runup_pct: float = 8.0
    require_price_above_sma: bool = True
    sma_period: int = 20
    min_volume_ratio: float = 1.5
    sentiment_weight: float = 0.5
    headline_weight: float = 0.2
    volume_weight: float = 0.3
    momentum_buy_score: float = 0.6
    reversion_enabled: bool = True
    reversion_live: bool = False
    rsi_period: int = 2
    rsi_oversold: float = 10.0
    rsi_exit: float = 65.0
    reversion_trend_sma_period: int = 200
    reversion_min_volume_ratio: float = 1.3
    reversion_sentiment_veto: float = -5.0
    reversion_max_hold_days: int = 5


@dataclass
class _Cfg:
    strategy: _StrategyCfg = field(default_factory=_StrategyCfg)


@dataclass
class _Snap:
    last: float
    change_pct: Optional[float] = None
    sma: Optional[float] = None
    volume_ratio: Optional[float] = None
    trend_sma: Optional[float] = None
    rsi: Optional[float] = None


class _NullRecorder:
    def __getattr__(self, _name):
        return lambda *a, **k: None


class _FakeStrategy:
    """Duck-typed stand-in for SentimentStrategy providing only what the
    methods under test touch (self.cfg, self.recorder)."""
    def __init__(self, cfg=None):
        self.cfg = cfg or _Cfg()
        self.recorder = _NullRecorder()


def _sentiment(score, headlines):
    return SentimentResult(symbol="TEST", score=score, label="positive" if score > 0 else "neutral",
                           rationale="test", article_count=headlines)


# ---- Path A: weighted composite ----------------------------------------

def test_momentum_path_partial_signals_now_clear_where_hard_gate_used_to_block():
    """The real-world case that motivated v2: AAPL on 2026-09-02 scored 6.0
    (below the old hard buy_threshold=8.0) with 9 headlines and 1.4x volume -
    blocked under v1, should clear under v2's weighted composite."""
    fake = _FakeStrategy()
    snap = _Snap(last=100.0, change_pct=1.0, sma=95.0, volume_ratio=1.4)
    sentiment = _sentiment(score=6.0, headlines=9)
    reason = SentimentStrategy._evaluate_momentum_path(fake, "AAPL", sentiment, snap)
    assert reason is not None
    assert "composite score" in reason


def test_momentum_path_weak_signal_still_skipped():
    fake = _FakeStrategy()
    snap = _Snap(last=100.0, change_pct=0.5, sma=95.0, volume_ratio=0.5)
    sentiment = _sentiment(score=0.0, headlines=2)
    reason = SentimentStrategy._evaluate_momentum_path(fake, "WEAK", sentiment, snap)
    assert reason is None


def test_momentum_path_runup_is_still_a_hard_gate():
    fake = _FakeStrategy()
    snap = _Snap(last=120.0, change_pct=20.0, sma=95.0, volume_ratio=2.0)
    sentiment = _sentiment(score=10.0, headlines=10)
    reason = SentimentStrategy._evaluate_momentum_path(fake, "CRM", sentiment, snap)
    assert reason is None  # even a perfect sentiment/volume score can't buy a 20% pop


def test_momentum_path_below_sma_is_still_a_hard_gate():
    fake = _FakeStrategy()
    snap = _Snap(last=90.0, change_pct=1.0, sma=95.0, volume_ratio=2.0)
    sentiment = _sentiment(score=10.0, headlines=10)
    reason = SentimentStrategy._evaluate_momentum_path(fake, "AMZN", sentiment, snap)
    assert reason is None


# ---- Path B: RSI-2 mean-reversion ---------------------------------------

def test_reversion_path_fires_on_oversold_uptrend_volume_confirmed():
    fake = _FakeStrategy()
    snap = _Snap(last=185.0, sma=180.0, trend_sma=150.0, rsi=5.0, volume_ratio=1.5)
    sentiment = _sentiment(score=0.0, headlines=1)  # no strong opinion either way
    reason = SentimentStrategy._evaluate_reversion_path(fake, "DIP", sentiment, snap)
    assert reason is not None
    assert "RSI(2)=5.0" in reason


def test_reversion_path_not_oversold_no_signal():
    fake = _FakeStrategy()
    snap = _Snap(last=185.0, trend_sma=150.0, rsi=50.0, volume_ratio=1.5)
    sentiment = _sentiment(score=0.0, headlines=1)
    assert SentimentStrategy._evaluate_reversion_path(fake, "FLAT", sentiment, snap) is None


def test_reversion_path_below_long_term_trend_no_signal():
    fake = _FakeStrategy()
    snap = _Snap(last=100.0, trend_sma=150.0, rsi=5.0, volume_ratio=1.5)  # downtrend
    sentiment = _sentiment(score=0.0, headlines=1)
    assert SentimentStrategy._evaluate_reversion_path(fake, "DOWNTREND", sentiment, snap) is None


def test_reversion_path_blocked_by_low_volume():
    fake = _FakeStrategy()
    snap = _Snap(last=185.0, trend_sma=150.0, rsi=5.0, volume_ratio=0.8)
    sentiment = _sentiment(score=0.0, headlines=1)
    assert SentimentStrategy._evaluate_reversion_path(fake, "THIN", sentiment, snap) is None


def test_reversion_path_vetoed_by_bearish_sentiment():
    """An oversold dip with genuinely bad news behind it should NOT be
    treated as a buyable technical dip."""
    fake = _FakeStrategy()
    snap = _Snap(last=185.0, trend_sma=150.0, rsi=5.0, volume_ratio=1.5)
    sentiment = _sentiment(score=-6.0, headlines=8)
    assert SentimentStrategy._evaluate_reversion_path(fake, "BADNEWS", sentiment, snap) is None


def test_reversion_exit_on_rsi_recovery():
    fake = _FakeStrategy()
    snap = _Snap(last=190.0, rsi=70.0)
    lot = {"entry_time": None}
    reason = SentimentStrategy._reversion_exit_reason(fake, snap, lot)
    assert reason is not None
    assert "RSI(2)=70.0" in reason


def test_reversion_exit_on_max_hold_days():
    import time
    fake = _FakeStrategy()
    snap = _Snap(last=190.0, rsi=40.0)  # not RSI-exit-worthy
    lot = {"entry_time": time.time() - 6 * 86400.0}  # held 6 days, max is 5
    reason = SentimentStrategy._reversion_exit_reason(fake, snap, lot)
    assert reason is not None
    assert "max hold" in reason


def test_reversion_no_exit_when_neither_condition_met():
    import time
    fake = _FakeStrategy()
    snap = _Snap(last=190.0, rsi=40.0)
    lot = {"entry_time": time.time() - 1 * 86400.0}  # held 1 day
    assert SentimentStrategy._reversion_exit_reason(fake, snap, lot) is None
