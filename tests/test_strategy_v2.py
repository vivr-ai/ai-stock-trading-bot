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
from decimal import Decimal
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.sentiment.base import SentimentResult
from bot.trading.strategy import (
    SentimentStrategy,
    CycleStats,
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
    sell_severe_threshold: float = -8.0
    momentum_max_hold_days: int = 10
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
class _RiskCfg:
    trailing_stop_enabled: bool = False
    trailing_stop_pct: float = 7.0
    trailing_stop_activation_pct: float = 3.0
    trailing_stop_backstop_take_profit_pct: float = 50.0
    dry_run: bool = False


@dataclass
class _Cfg:
    strategy: _StrategyCfg = field(default_factory=_StrategyCfg)
    schedule: _ScheduleCfg = field(default_factory=_ScheduleCfg)
    risk: _RiskCfg = field(default_factory=_RiskCfg)


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


# ---- Strategy v3: two-tier sentiment exit + momentum time-based exit ----
# See docs/sell-strategy.md ("Sell Strategy Recommendation"): a moderate
# reading still needs sell_min_headlines of coverage to trust it; a SEVERE
# reading is trusted sooner, on less confirmation. Regression coverage for
# the 2026-09-22 ORCL incident (a bad-enough reading on thin coverage got no
# software action at all) and the "position going nowhere forever" gap
# momentum entries had but reversion entries didn't.

class _RecordingRecorder:
    """Like _NullRecorder, but keeps record_decision calls for assertions."""
    def __init__(self):
        self.decisions = []

    def record_decision(self, **kwargs):
        self.decisions.append(kwargs)

    def __getattr__(self, _name):
        return lambda *a, **k: None


def test_sentiment_exit_fires_on_moderate_reading_with_enough_headlines():
    fake = _FakeStrategy()
    sentiment = _sentiment(score=-6.5, headlines=4)
    reason = SentimentStrategy._sentiment_exit_reason(fake, "AAPL", sentiment)
    assert reason is not None
    assert "sentiment -6.5" in reason


def test_sentiment_exit_skips_and_records_on_thin_coverage_moderate_reading():
    """The ORCL case: -6.0 on 2 headlines (< the 3-headline minimum) isn't
    severe enough (> -8.0) to bypass the coverage check."""
    fake = _FakeStrategy()
    fake.recorder = _RecordingRecorder()
    sentiment = _sentiment(score=-6.0, headlines=2)
    reason = SentimentStrategy._sentiment_exit_reason(fake, "ORCL", sentiment)
    assert reason is None
    assert fake.recorder.decisions[-1]["reason"] == "too_few_headlines"


def test_sentiment_exit_fires_on_severe_reading_despite_thin_coverage():
    """A severe reading (<= -8.0) is trusted even on just 1 headline -
    the fix for the ORCL-style gap."""
    fake = _FakeStrategy()
    fake.recorder = _RecordingRecorder()
    sentiment = _sentiment(score=-9.0, headlines=1)
    reason = SentimentStrategy._sentiment_exit_reason(fake, "XYZ", sentiment)
    assert reason is not None
    assert "severe" in reason
    assert fake.recorder.decisions == []  # no skip recorded - it fired


def test_sentiment_exit_no_signal_above_threshold():
    fake = _FakeStrategy()
    sentiment = _sentiment(score=-2.0, headlines=5)
    assert SentimentStrategy._sentiment_exit_reason(fake, "CALM", sentiment) is None


def test_momentum_time_exit_fires_after_max_hold():
    import time
    fake = _FakeStrategy()
    lot = {"entry_time": time.time() - 11 * 86400.0}  # held 11 days, max is 10
    reason = SentimentStrategy._momentum_time_exit_reason(fake, lot)
    assert reason is not None
    assert "max hold" in reason


def test_momentum_time_exit_no_exit_before_max_hold():
    import time
    fake = _FakeStrategy()
    lot = {"entry_time": time.time() - 2 * 86400.0}  # held 2 days
    assert SentimentStrategy._momentum_time_exit_reason(fake, lot) is None


def test_momentum_time_exit_disabled_when_zero():
    import time
    fake = _FakeStrategy(cfg=_Cfg(strategy=_StrategyCfg(momentum_max_hold_days=0)))
    lot = {"entry_time": time.time() - 999 * 86400.0}
    assert SentimentStrategy._momentum_time_exit_reason(fake, lot) is None


def test_momentum_time_exit_fails_open_with_no_lot():
    """Open-lot state lost AND no durable entry_time recoverable (e.g. the
    DB fallback in _process_symbol also came back empty) - fail open, same
    convention as every other exit rule in this file."""
    fake = _FakeStrategy()
    assert SentimentStrategy._momentum_time_exit_reason(fake, None) is None
    assert SentimentStrategy._momentum_time_exit_reason(fake, {}) is None


# ---- _resolve_lot: durable fallback when local open-lot state is lost ---

class _FakeState:
    def __init__(self, lot=None):
        self._lot = lot

    def peek_open(self, symbol):
        return self._lot


class _FakeRecorderWithLastBuy:
    def __init__(self, last_buy=None):
        self._last_buy = last_buy

    def get_last_buy_trade(self, symbol):
        return self._last_buy

    def __getattr__(self, _name):
        return lambda *a, **k: None


def test_resolve_lot_uses_local_state_when_present():
    """The normal case: local open-lot state hasn't been lost - no DB
    round-trip needed."""
    fake = _FakeStrategy()
    fake.state = _FakeState(lot={"entry_path": "mean_reversion", "entry_time": 123.0})
    fake.recorder = _FakeRecorderWithLastBuy(last_buy={"entry_path": "sentiment_momentum"})
    lot = SentimentStrategy._resolve_lot(fake, "AAPL")
    assert lot == {"entry_path": "mean_reversion", "entry_time": 123.0}


def test_resolve_lot_recovers_entry_path_and_time_from_durable_trades_row():
    """Local state lost (e.g. a Railway redeploy) - recovers entry_path AND
    entry_time from the trades table instead of coming back empty, so a
    live mean-reversion position doesn't silently fall back to the
    sentiment-exit rule after a redeploy."""
    fake = _FakeStrategy()
    fake.state = _FakeState(lot=None)
    buy_ts = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    fake.recorder = _FakeRecorderWithLastBuy(
        last_buy={"entry_path": "mean_reversion", "ts": buy_ts})
    lot = SentimentStrategy._resolve_lot(fake, "NVDA")
    assert lot["entry_path"] == "mean_reversion"
    assert lot["entry_time"] == buy_ts.timestamp()


def test_resolve_lot_returns_none_when_nothing_recoverable():
    """State lost AND no matching trades row (e.g. a manually-opened
    position) - fails open, same as before this fallback existed."""
    fake = _FakeStrategy()
    fake.state = _FakeState(lot=None)
    fake.recorder = _FakeRecorderWithLastBuy(last_buy=None)
    assert SentimentStrategy._resolve_lot(fake, "MANUAL") is None


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


# ---- _maybe_trailing_stop_exit -----------------------------------------
# Software trailing-stop layer (risk.trailing_stop_enabled) - runs ahead of
# either entry path's own sell rule in _process_symbol. _do_sell itself
# needs a broker/trade_logger/closed_trade_logger too heavy for this fake
# (same reasoning as _FakeShadowStrategy above), so it's stubbed here -
# these tests are only about the peak-tracking/activation/pullback
# arithmetic and the fail-open conditions, not _do_sell's own
# already-tested behavior.

class _FakeTrailingRecorder:
    def __init__(self, last_buy=None, peak=None):
        self._last_buy = last_buy
        self._peak = peak
        self.peak_updates = []

    def get_last_buy_trade(self, symbol):
        return self._last_buy

    def get_position_peak(self, symbol):
        return self._peak

    def update_position_peak(self, symbol, price):
        self.peak_updates.append((symbol, price))

    def __getattr__(self, _name):
        return lambda *a, **k: None


class _FakeTrailingBroker:
    def __init__(self, price="unset"):
        self._price = price

    def latest_price(self, symbol):
        if self._price == "unset":
            raise AssertionError("latest_price should not have been called")
        return self._price


class _FakeTrailingState:
    def __init__(self):
        self.exits = []

    def mark_exit(self, symbol):
        self.exits.append(symbol)


class _FakeTrailingStrategy:
    def __init__(self, cfg=None, recorder=None, broker=None, sell_succeeds=True):
        self.cfg = cfg or _Cfg()
        self.recorder = recorder
        self.broker = broker
        self.state = _FakeTrailingState()
        self.sells = []
        self._sell_succeeds = sell_succeeds

    def _do_sell(self, symbol, sentiment, reason, entry_path="sentiment_momentum"):
        self.sells.append({"symbol": symbol, "reason": reason, "entry_path": entry_path})
        return self._sell_succeeds


def _trailing_cfg(**risk_kwargs):
    return _Cfg(risk=_RiskCfg(**risk_kwargs))


def test_trailing_stop_noop_when_disabled():
    """Off (the default) - never even looks at price/DB, matching the
    method's early-return before any recorder/broker call."""
    fake = _FakeTrailingStrategy(cfg=_trailing_cfg(trailing_stop_enabled=False),
                                  recorder=_FakeTrailingRecorder(),
                                  broker=_FakeTrailingBroker())  # unset -> raises if hit
    fired = SentimentStrategy._maybe_trailing_stop_exit(
        fake, "AAPL", _sentiment(5.0, 4), "sentiment_momentum", {}, CycleStats())
    assert fired is False
    assert fake.sells == []


def test_trailing_stop_fails_open_when_price_unavailable():
    class _NoPriceBroker(_FakeTrailingBroker):
        def latest_price(self, symbol):
            return None

    fake = _FakeTrailingStrategy(cfg=_trailing_cfg(trailing_stop_enabled=True),
                                  recorder=_FakeTrailingRecorder(), broker=_NoPriceBroker())
    fired = SentimentStrategy._maybe_trailing_stop_exit(
        fake, "AAPL", _sentiment(5.0, 4), "sentiment_momentum", {}, CycleStats())
    assert fired is False
    assert fake.sells == []


def test_trailing_stop_fails_open_when_no_durable_buy_row():
    """No trades-table row for this symbol (e.g. a manually-adopted
    position with no history) - must not fall back to any other price
    source or raise, just skip this cycle's check."""
    fake = _FakeTrailingStrategy(cfg=_trailing_cfg(trailing_stop_enabled=True),
                                  recorder=_FakeTrailingRecorder(last_buy=None),
                                  broker=_FakeTrailingBroker(110.0))
    fired = SentimentStrategy._maybe_trailing_stop_exit(
        fake, "AAPL", _sentiment(5.0, 4), "sentiment_momentum", {}, CycleStats())
    assert fired is False
    assert fake.sells == []


def test_trailing_stop_not_armed_below_activation_but_still_tracks_peak():
    """Up only 2% from entry, activation threshold is 3% - too early to
    arm, but the peak should still ratchet so it's ready the moment
    activation is crossed."""
    recorder = _FakeTrailingRecorder(last_buy={"price": 100.0}, peak=None)
    fake = _FakeTrailingStrategy(
        cfg=_trailing_cfg(trailing_stop_enabled=True, trailing_stop_activation_pct=3.0,
                           trailing_stop_pct=7.0),
        recorder=recorder, broker=_FakeTrailingBroker(102.0))
    fired = SentimentStrategy._maybe_trailing_stop_exit(
        fake, "AAPL", _sentiment(5.0, 4), "sentiment_momentum", {}, CycleStats())
    assert fired is False
    assert fake.sells == []
    assert recorder.peak_updates == [("AAPL", 102.0)]


def test_trailing_stop_armed_but_no_pullback_yet_is_noop():
    """Up 10% from entry (armed), price is exactly the peak - 0% pullback,
    nowhere near the 7% trailing_stop_pct trigger."""
    recorder = _FakeTrailingRecorder(last_buy={"price": 100.0}, peak=108.0)
    fake = _FakeTrailingStrategy(
        cfg=_trailing_cfg(trailing_stop_enabled=True, trailing_stop_activation_pct=3.0,
                           trailing_stop_pct=7.0),
        recorder=recorder, broker=_FakeTrailingBroker(110.0))
    fired = SentimentStrategy._maybe_trailing_stop_exit(
        fake, "AAPL", _sentiment(5.0, 4), "sentiment_momentum", {}, CycleStats())
    assert fired is False
    assert fake.sells == []


def test_trailing_stop_fires_on_pullback_from_peak():
    """Peaked at 120 (up 20% from entry), now back to 110 - a 8.3% pullback
    from peak, over the 7% trigger, while still +10% above entry."""
    recorder = _FakeTrailingRecorder(last_buy={"price": 100.0}, peak=120.0)
    fake = _FakeTrailingStrategy(
        cfg=_trailing_cfg(trailing_stop_enabled=True, trailing_stop_activation_pct=3.0,
                           trailing_stop_pct=7.0),
        recorder=recorder, broker=_FakeTrailingBroker(110.0))
    stats = CycleStats()
    sector_counts = {"tech": 1}

    fired = SentimentStrategy._maybe_trailing_stop_exit(
        fake, "AAPL", _sentiment(5.0, 4), "sentiment_momentum", sector_counts, stats)

    assert fired is True
    assert stats.sells == 1
    assert sector_counts["tech"] == 0
    assert fake.state.exits == ["AAPL"]
    assert len(fake.sells) == 1
    sold = fake.sells[0]
    assert sold["symbol"] == "AAPL"
    assert sold["entry_path"] == "sentiment_momentum"
    assert "trailing stop" in sold["reason"]
    assert "120.00" in sold["reason"]
    assert "110.00" in sold["reason"]


def test_trailing_stop_handles_decimal_entry_price_from_db():
    """Regression test for a real production incident: psycopg2 returns
    every NUMERIC column (trades.price included) as decimal.Decimal, not
    float, so a real get_last_buy_trade() call returns {"price":
    Decimal("100.0")} - never the plain float this file's other fakes use.
    Before the fix (bot/persistence/db.py's _row_to_dict), `price -
    entry_price` at the top of this method mixed a float (from
    broker.latest_price) with a Decimal and raised TypeError on every
    single cycle for every held position from the moment
    RISK_TRAILING_STOP_ENABLED was first turned on - silently disabling
    not just the trailing stop but the sentiment-exit/mean-reversion-exit
    checks below it too, since the exception propagated out of
    _process_symbol before either ran. This fake intentionally returns a
    Decimal to make sure that class of bug can't silently come back."""
    recorder = _FakeTrailingRecorder(last_buy={"price": Decimal("100.0")}, peak=120.0)
    fake = _FakeTrailingStrategy(
        cfg=_trailing_cfg(trailing_stop_enabled=True, trailing_stop_activation_pct=3.0,
                           trailing_stop_pct=7.0),
        recorder=recorder, broker=_FakeTrailingBroker(110.0))

    fired = SentimentStrategy._maybe_trailing_stop_exit(
        fake, "AAPL", _sentiment(5.0, 4), "sentiment_momentum", {"tech": 1}, CycleStats())

    assert fired is True
    assert len(fake.sells) == 1


def test_trailing_stop_fires_for_mean_reversion_entries_too():
    """The trailing-stop layer is a price-only backstop that runs ahead of
    EITHER entry path's own sell rule - not exclusive to sentiment-momentum
    entries."""
    recorder = _FakeTrailingRecorder(last_buy={"price": 50.0}, peak=60.0)
    fake = _FakeTrailingStrategy(
        cfg=_trailing_cfg(trailing_stop_enabled=True, trailing_stop_activation_pct=3.0,
                           trailing_stop_pct=5.0),
        recorder=recorder, broker=_FakeTrailingBroker(56.0))
    fired = SentimentStrategy._maybe_trailing_stop_exit(
        fake, "MRK", _sentiment(0.0, 4), "mean_reversion", {"healthcare": 1}, CycleStats())
    assert fired is True
    assert fake.sells[0]["entry_path"] == "mean_reversion"


def test_trailing_stop_recorder_error_fails_open():
    """A DB hiccup on the last-buy lookup must never raise into the
    trading loop - same fail-open posture as every other Recorder
    integration point in this file."""
    class _BrokenRecorder(_FakeTrailingRecorder):
        def get_last_buy_trade(self, symbol):
            raise RuntimeError("connection refused")

    fake = _FakeTrailingStrategy(cfg=_trailing_cfg(trailing_stop_enabled=True),
                                  recorder=_BrokenRecorder(), broker=_FakeTrailingBroker(110.0))
    fired = SentimentStrategy._maybe_trailing_stop_exit(
        fake, "AAPL", _sentiment(5.0, 4), "sentiment_momentum", {}, CycleStats())
    assert fired is False
    assert fake.sells == []


def test_trailing_stop_no_exit_bookkeeping_when_sell_did_not_go_through():
    """If _do_sell skips or fails, the position is still held - it must not
    free a sector slot, start a re-entry cooldown, or count as a sell."""
    recorder = _FakeTrailingRecorder(last_buy={"price": 100.0}, peak=120.0)
    fake = _FakeTrailingStrategy(
        cfg=_trailing_cfg(trailing_stop_enabled=True, trailing_stop_activation_pct=3.0,
                           trailing_stop_pct=7.0),
        recorder=recorder, broker=_FakeTrailingBroker(110.0), sell_succeeds=False)
    stats = CycleStats()
    sector_counts = {"tech": 1}

    fired = SentimentStrategy._maybe_trailing_stop_exit(
        fake, "AAPL", _sentiment(5.0, 4), "sentiment_momentum", sector_counts, stats)

    assert fired is True           # handled: don't retry the same close this cycle
    assert len(fake.sells) == 1    # a close WAS attempted
    assert stats.sells == 0
    assert sector_counts["tech"] == 1
    assert fake.state.exits == []


# ---- _do_sell vs. standing protective orders ---------------------------
# Regression tests for the 2026-09-20..23 production incident: _do_sell
# skipped any symbol in broker.pending_order_symbols() - but every held
# position ALWAYS has its protective stop/take-profit order open, so every
# software exit (sentiment, trailing stop, mean-reversion) was skipped on
# every cycle ("SELL GOOGL skipped: a working order already exists...", 13
# cycles in a row). The earlier fakes in this file stub _do_sell out
# entirely, which is how this went unnoticed; these call the real one.

class _FakeSellBroker:
    def __init__(self, open_order_symbols=()):
        self._open = set(open_order_symbols)  # standing protective orders
        self.closed = []

    def pending_order_symbols(self):
        return set(self._open)

    def close_position(self, symbol):
        from bot.trading.alpaca_client import PlacedOrder
        self.closed.append(symbol)
        self._open.discard(symbol)
        return PlacedOrder(order_id="ord-1", symbol=symbol, side="sell", qty=10,
                           stop_price=0.0, take_profit_price=0.0, status="accepted",
                           filled_avg_price=None)

    def latest_price(self, symbol):
        return 100.0


class _FakeSellRecorder:
    def __init__(self):
        self.decisions = []

    def record_decision(self, **kwargs):
        self.decisions.append(kwargs)

    def __getattr__(self, _name):
        return lambda *a, **k: None


class _FakeSellStrategy:
    def __init__(self, broker, protected_this_cycle=()):
        self.cfg = _Cfg()  # risk.dry_run defaults to False -> real close path
        self.broker = broker
        self.recorder = _FakeSellRecorder()
        self.trade_logger = _NullRecorder()
        self.closed_trade_logger = None
        self._protected_this_cycle = set(protected_this_cycle)


def test_do_sell_closes_position_that_has_a_standing_protective_order():
    """The normal case: held position, its protective stop/take-profit is
    open at the broker (as it always is), bearish signal -> must close.
    close_position() cancels those legs itself."""
    broker = _FakeSellBroker(open_order_symbols={"GOOGL"})
    fake = _FakeSellStrategy(broker)

    sold = SentimentStrategy._do_sell(fake, "GOOGL", _sentiment(-9.0, 10), reason="sentiment -9.0 < -5.0")

    assert sold is True
    assert broker.closed == ["GOOGL"]
    assert not any(d.get("decision") == "sell_skipped" for d in fake.recorder.decisions)


def test_do_sell_defers_only_when_protected_this_same_cycle():
    """The one race the guard is for: _adopt_legacy_positions JUST submitted
    a protective order for this symbol this cycle - defer to next cycle."""
    broker = _FakeSellBroker(open_order_symbols={"NVDA"})
    fake = _FakeSellStrategy(broker, protected_this_cycle={"NVDA"})

    sold = SentimentStrategy._do_sell(fake, "NVDA", _sentiment(-9.0, 10), reason="sentiment -9.0 < -5.0")

    assert sold is False
    assert broker.closed == []
    assert fake.recorder.decisions[-1]["reason"] == "protected_this_cycle"


def test_do_sell_returns_false_when_close_fails():
    class _FailingBroker(_FakeSellBroker):
        def close_position(self, symbol):
            return None

    fake = _FakeSellStrategy(_FailingBroker())
    assert SentimentStrategy._do_sell(fake, "ORCL", _sentiment(-9.0, 5), reason="x") is False


# ---- AlpacaBroker.cancel_orders_for waits for cancels to settle --------

class _FakeOrder:
    def __init__(self, id_, symbol):
        self.id = id_
        self.symbol = symbol


class _FakeTradingClient:
    """Alpaca cancels asynchronously: the order stays in the open list for a
    couple of polls after cancel_order_by_id (pending_cancel)."""
    def __init__(self, orders, polls_until_cleared=2):
        self._orders = list(orders)
        self._cancelled = set()
        self._polls_after_cancel = 0
        self._polls_until_cleared = polls_until_cleared
        self.cancel_calls = []

    def get_orders(self, _req):
        if self._cancelled:
            self._polls_after_cancel += 1
            if self._polls_after_cancel > self._polls_until_cleared:
                self._orders = [o for o in self._orders if o.id not in self._cancelled]
        return list(self._orders)

    def cancel_order_by_id(self, id_):
        self.cancel_calls.append(id_)
        self._cancelled.add(id_)


def _bare_broker(trading):
    from bot.trading.alpaca_client import AlpacaBroker
    b = AlpacaBroker.__new__(AlpacaBroker)  # skip __init__ (no network/credentials)
    b._trading = trading
    b._retry = lambda fn, op_name: fn()
    return b


def test_cancel_orders_for_waits_until_orders_cleared():
    pytest = __import__("pytest")
    pytest.importorskip("alpaca")
    trading = _FakeTradingClient([_FakeOrder("a", "GOOGL"), _FakeOrder("b", "MSFT")],
                                 polls_until_cleared=2)
    broker = _bare_broker(trading)

    broker.cancel_orders_for("GOOGL", settle_timeout_s=2.0, poll_interval_s=0.01)

    assert trading.cancel_calls == ["a"]  # only this symbol's order
    assert [o.symbol for o in trading.get_orders(None)] == ["MSFT"]


def test_cancel_orders_for_gives_up_after_timeout_without_raising():
    pytest = __import__("pytest")
    pytest.importorskip("alpaca")
    trading = _FakeTradingClient([_FakeOrder("a", "GOOGL")], polls_until_cleared=10_000)
    broker = _bare_broker(trading)

    t0 = time_module.monotonic()
    broker.cancel_orders_for("GOOGL", settle_timeout_s=0.1, poll_interval_s=0.01)
    assert time_module.monotonic() - t0 < 1.0  # bounded; close_position then tries anyway
