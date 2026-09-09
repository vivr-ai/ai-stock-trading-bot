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
import time as time_module
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.sentiment.base import SentimentResult
from bot.trading.strategy import (
    SentimentStrategy,
    SHADOW_VERDICT_MIN_CLOSED,
    SHADOW_VERDICT_MIN_WEEKS,
)


@dataclass
class _ScheduleCfg:
    market_timezone: str = "America/New_York"


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
    volume_lookback_days: int = 20
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
    schedule: _ScheduleCfg = field(default_factory=_ScheduleCfg)


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


# ---- Path B shadow-position lifecycle (_maybe_resolve_shadow_position) --
# These exercise the durable Postgres-backed shadow tracking added alongside
# the Shadow vs Live dashboard page: get_open_shadow_position (see
# bot/persistence/db.py) is the read-back of the most recent
# 'reversion_shadow_buy' decision with no later 'reversion_shadow_exit' -
# faked here rather than hit a real DB.

class _FakeShadowRecorder:
    def __init__(self, open_shadow=None):
        self._open_shadow = open_shadow
        self.decisions = []

    def get_open_shadow_position(self, symbol):
        return self._open_shadow

    def record_decision(self, **kwargs):
        self.decisions.append(kwargs)

    def __getattr__(self, _name):
        return lambda *a, **k: None


class _FakeShadowBroker:
    """market_snapshot returns a fixed snap; raises if called when the test
    expects _maybe_resolve_shadow_position to short-circuit before ever
    needing market data (nothing open)."""
    def __init__(self, snap="unset"):
        self._snap = snap

    def market_snapshot(self, *args, **kwargs):
        if self._snap == "unset":
            raise AssertionError("market_snapshot should not have been called")
        return self._snap


class _FakeShadowStrategy:
    # _maybe_resolve_shadow_position (called unbound, below) itself calls
    # self._reversion_exit_reason(...) - bind the real implementation here
    # too (it only touches self.cfg.strategy, which this fake has) rather
    # than reimplementing its RSI-exit/max-hold logic a second time in a
    # fake and risking the two drifting apart.
    _reversion_exit_reason = SentimentStrategy._reversion_exit_reason

    def __init__(self, cfg=None, recorder=None, broker=None):
        self.cfg = cfg or _Cfg()
        self.recorder = recorder
        self.broker = broker


def test_maybe_resolve_shadow_position_noop_when_nothing_open():
    recorder = _FakeShadowRecorder(open_shadow=None)
    fake = _FakeShadowStrategy(recorder=recorder, broker=_FakeShadowBroker())  # broker: unset -> raises if hit
    SentimentStrategy._maybe_resolve_shadow_position(fake, "AVGO")
    assert recorder.decisions == []


def test_maybe_resolve_shadow_position_exits_on_rsi_recovery():
    entry_ts = datetime.now(timezone.utc) - timedelta(days=1)
    open_shadow = {"ts": entry_ts, "price": 100.0, "reason": "RSI(2)=4.0 oversold"}
    recorder = _FakeShadowRecorder(open_shadow=open_shadow)
    snap = _Snap(last=110.0, rsi=70.0)  # rsi_exit default is 65.0
    fake = _FakeShadowStrategy(recorder=recorder, broker=_FakeShadowBroker(snap))

    SentimentStrategy._maybe_resolve_shadow_position(fake, "AVGO")

    assert len(recorder.decisions) == 1
    logged = recorder.decisions[0]
    assert logged["decision"] == "reversion_shadow_exit"
    assert logged["symbol"] == "AVGO"
    assert logged["entry_path"] == "mean_reversion"
    assert logged["extra"]["entry_price"] == 100.0
    assert logged["extra"]["exit_price"] == 110.0
    assert abs(logged["extra"]["pnl_pct"] - 10.0) < 1e-9  # (110-100)/100 * 100


def test_maybe_resolve_shadow_position_exits_on_max_hold_days():
    entry_ts = datetime.now(timezone.utc) - timedelta(days=6)  # max is 5
    open_shadow = {"ts": entry_ts, "price": 100.0, "reason": "RSI(2)=4.0 oversold"}
    recorder = _FakeShadowRecorder(open_shadow=open_shadow)
    snap = _Snap(last=95.0, rsi=40.0)  # not RSI-exit-worthy on its own
    fake = _FakeShadowStrategy(recorder=recorder, broker=_FakeShadowBroker(snap))

    SentimentStrategy._maybe_resolve_shadow_position(fake, "AVGO")

    assert len(recorder.decisions) == 1
    logged = recorder.decisions[0]
    assert logged["decision"] == "reversion_shadow_exit"
    assert "max hold" in logged["reason"]
    assert abs(logged["extra"]["pnl_pct"] - (-5.0)) < 1e-9  # (95-100)/100 * 100


def test_maybe_resolve_shadow_position_stays_open_when_no_exit_condition():
    entry_ts = datetime.now(timezone.utc) - timedelta(days=1)  # well under max hold
    open_shadow = {"ts": entry_ts, "price": 100.0, "reason": "RSI(2)=4.0 oversold"}
    recorder = _FakeShadowRecorder(open_shadow=open_shadow)
    snap = _Snap(last=102.0, rsi=40.0)  # oversold recovery in progress, not exit-worthy yet
    fake = _FakeShadowStrategy(recorder=recorder, broker=_FakeShadowBroker(snap))

    SentimentStrategy._maybe_resolve_shadow_position(fake, "AVGO")

    assert recorder.decisions == []  # left open for a later cycle


def test_maybe_resolve_shadow_position_recorder_error_fails_open():
    """A DB hiccup on the read-back must never raise into the trading loop -
    same fail-open posture as every other Recorder integration point."""
    class _BrokenRecorder(_FakeShadowRecorder):
        def get_open_shadow_position(self, symbol):
            raise RuntimeError("connection refused")

    fake = _FakeShadowStrategy(recorder=_BrokenRecorder(), broker=_FakeShadowBroker())
    SentimentStrategy._maybe_resolve_shadow_position(fake, "AVGO")  # must not raise


# ---- _maybe_notify_shadow_verdict_ready --------------------------------
# The one-time "Path B shadow data ready for a verdict" alert (see
# SentimentStrategy._maybe_notify_shadow_verdict_ready and
# Recorder.get_shadow_verdict_progress / has_ever_notified in
# bot/persistence/db.py) - fires once the shadow round-trip sample crosses
# SHADOW_VERDICT_MIN_CLOSED closed episodes and SHADOW_VERDICT_MIN_WEEKS
# weeks observed, matching the dashboard's own Shadow vs Live readiness bar.

class _FakeVerdictRecorder:
    def __init__(self, already_notified=False, progress=None):
        self._already_notified = already_notified
        self._progress = progress
        self.notifications = []

    def has_ever_notified(self, type_):
        return self._already_notified

    def get_shadow_verdict_progress(self):
        return self._progress

    def record_notification(self, **kwargs):
        self.notifications.append(kwargs)

    def __getattr__(self, _name):
        return lambda *a, **k: None


def _verdict_cfg(reversion_enabled=True, reversion_live=False):
    return _Cfg(strategy=_StrategyCfg(reversion_enabled=reversion_enabled,
                                       reversion_live=reversion_live))


def test_shadow_verdict_notify_skipped_when_reversion_disabled():
    recorder = _FakeVerdictRecorder(progress={
        "closed_count": 999, "since_ts": datetime.now(timezone.utc) - timedelta(weeks=52),
    })
    fake = _FakeShadowStrategy(cfg=_verdict_cfg(reversion_enabled=False), recorder=recorder)
    SentimentStrategy._maybe_notify_shadow_verdict_ready(fake)
    assert recorder.notifications == []


def test_shadow_verdict_notify_skipped_once_path_b_is_live():
    """Once Path B trades for real, 'is the shadow sample big enough yet' is
    a moot question - never fires."""
    recorder = _FakeVerdictRecorder(progress={
        "closed_count": 999, "since_ts": datetime.now(timezone.utc) - timedelta(weeks=52),
    })
    fake = _FakeShadowStrategy(cfg=_verdict_cfg(reversion_live=True), recorder=recorder)
    SentimentStrategy._maybe_notify_shadow_verdict_ready(fake)
    assert recorder.notifications == []


def test_shadow_verdict_notify_skipped_when_already_notified():
    recorder = _FakeVerdictRecorder(already_notified=True, progress={
        "closed_count": 999, "since_ts": datetime.now(timezone.utc) - timedelta(weeks=52),
    })
    fake = _FakeShadowStrategy(cfg=_verdict_cfg(), recorder=recorder)
    SentimentStrategy._maybe_notify_shadow_verdict_ready(fake)
    assert recorder.notifications == []


def test_shadow_verdict_notify_skipped_when_no_progress_yet():
    recorder = _FakeVerdictRecorder(progress=None)
    fake = _FakeShadowStrategy(cfg=_verdict_cfg(), recorder=recorder)
    SentimentStrategy._maybe_notify_shadow_verdict_ready(fake)
    assert recorder.notifications == []


def test_shadow_verdict_notify_skipped_below_closed_count_threshold():
    recorder = _FakeVerdictRecorder(progress={
        "closed_count": SHADOW_VERDICT_MIN_CLOSED - 1,
        "since_ts": datetime.now(timezone.utc) - timedelta(weeks=SHADOW_VERDICT_MIN_WEEKS + 10),
    })
    fake = _FakeShadowStrategy(cfg=_verdict_cfg(), recorder=recorder)
    SentimentStrategy._maybe_notify_shadow_verdict_ready(fake)
    assert recorder.notifications == []


def test_shadow_verdict_notify_skipped_below_weeks_threshold():
    recorder = _FakeVerdictRecorder(progress={
        "closed_count": SHADOW_VERDICT_MIN_CLOSED + 50,
        "since_ts": datetime.now(timezone.utc) - timedelta(weeks=SHADOW_VERDICT_MIN_WEEKS - 1),
    })
    fake = _FakeShadowStrategy(cfg=_verdict_cfg(), recorder=recorder)
    SentimentStrategy._maybe_notify_shadow_verdict_ready(fake)
    assert recorder.notifications == []


def test_shadow_verdict_notify_fires_once_threshold_crossed():
    recorder = _FakeVerdictRecorder(progress={
        "closed_count": SHADOW_VERDICT_MIN_CLOSED,
        "since_ts": datetime.now(timezone.utc) - timedelta(weeks=SHADOW_VERDICT_MIN_WEEKS, hours=1),
    })
    fake = _FakeShadowStrategy(cfg=_verdict_cfg(), recorder=recorder)
    SentimentStrategy._maybe_notify_shadow_verdict_ready(fake)
    assert len(recorder.notifications) == 1
    notified = recorder.notifications[0]
    assert notified["type_"] == "shadow_verdict_ready"
    assert notified["metadata"]["closed_count"] == SHADOW_VERDICT_MIN_CLOSED


def test_shadow_verdict_notify_progress_lookup_error_fails_silent():
    class _BrokenRecorder(_FakeVerdictRecorder):
        def get_shadow_verdict_progress(self):
            raise RuntimeError("connection refused")

    fake = _FakeShadowStrategy(cfg=_verdict_cfg(), recorder=_BrokenRecorder())
    SentimentStrategy._maybe_notify_shadow_verdict_ready(fake)  # must not raise
