"""Stage 4: sentiment exit live / shadow / off."""
from collections import Counter
from types import SimpleNamespace

import pytest

from bot.config import ReliabilityConfig
from bot.trading.strategy import CycleStats, SentimentStrategy


def make(mode):
    s = SentimentStrategy.__new__(SentimentStrategy)
    sells, decisions = [], []
    sent = SimpleNamespace(score=-9.0, label="negative", article_count=8, positive_count=0,
                           negative_count=3, rationale="0 bullish vs 3 bearish of 8 headlines")
    s.cfg = SimpleNamespace(
        news=SimpleNamespace(lookback_hours=12, max_articles_per_symbol=15),
        strategy=SimpleNamespace(reversion_enabled=False, reversion_live=False, sell_threshold=-5.0,
                                 sell_min_headlines=3, sell_severe_threshold=-9.0),
        reliability=ReliabilityConfig(sentiment_exit_mode=mode),
    )
    s.news = SimpleNamespace(fetch=lambda *a: [])
    s.analyzer = SimpleNamespace(analyze=lambda sym, arts: sent)
    s.recorder = SimpleNamespace(record_decision=lambda **k: decisions.append(k))
    s._resolve_lot = lambda sym: {"entry_path": "sentiment_momentum", "entry_price": 100.0,
                                  "entry_time": 1.0}
    s._maybe_trailing_stop_exit = lambda *a: False
    s._momentum_time_exit_reason = lambda sym, lot: None
    s._do_sell = lambda sym, sent, reason, entry_path: sells.append(reason) or True
    s.state = SimpleNamespace(mark_exit=lambda sym: None)
    s._position_details_cache = {"AAPL": SimpleNamespace(avg_entry_price=100.0, current_price=95.0,
                                                         qty=10.0, unrealized_pl=-50.0)}
    s.broker = SimpleNamespace(latest_price=lambda sym: pytest.fail("cached price expected"))
    return s, sells, decisions


def run(s):
    stats = CycleStats()
    s._process_symbol("AAPL", {}, 0.0, {"AAPL": 10}, set(), Counter({"tech": 1}), True, stats)
    return stats


def test_live_mode_sells_as_today():
    s, sells, decisions = make("live")
    stats = run(s)
    assert len(sells) == 1 and stats.sells == 1
    assert not any(d["decision"] == "sentiment_exit_shadow" for d in decisions)


def test_shadow_mode_records_full_context_and_never_sells():
    s, sells, decisions = make("shadow")
    stats = run(s)
    assert sells == [] and stats.sells == 0
    shadow = [d for d in decisions if d["decision"] == "sentiment_exit_shadow"]
    assert len(shadow) == 1
    d = shadow[0]
    assert d["symbol"] == "AAPL" and d["sentiment_score"] == -9.0 and d["headline_count"] == 8
    assert d["reason"].startswith("sentiment") and d["price"] == 95.0
    assert d["extra"]["mode"] == "shadow" and d["extra"]["position_qty"] == 10.0
    assert d["extra"]["avg_entry_price"] == 100.0 and round(d["extra"]["unrealized_pct"], 2) == -5.0


def test_shadow_mode_still_runs_the_time_exit():
    s, sells, _ = make("shadow")
    s._momentum_time_exit_reason = lambda sym, lot: "max hold of 10 day(s) reached (10.2d)"
    run(s)
    assert sells == ["max hold of 10 day(s) reached (10.2d)"]


def test_off_mode_does_not_evaluate():
    s, sells, decisions = make("off")
    s._sentiment_exit_reason = lambda *a: pytest.fail("must not be evaluated")
    run(s)
    assert sells == [] and not any(d["decision"] == "sentiment_exit_shadow" for d in decisions)


def test_invalid_mode_rejected(monkeypatch):
    from bot import config

    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setenv("STRATEGY_SENTIMENT_EXIT_MODE", "maybe")
    with pytest.raises(ValueError, match="live, shadow or off"):
        config.load_config("/nonexistent.ini")


def test_default_mode_is_live(monkeypatch):
    from bot import config

    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.delenv("STRATEGY_SENTIMENT_EXIT_MODE", raising=False)
    cfg = config.load_config("/nonexistent.ini")
    assert cfg.reliability.sentiment_exit_mode == "live"
    assert cfg.reliability.trailing_v2_enabled is False and cfg.reliability.exit_manager_enabled is False
    assert cfg.reliability.lease_enabled is False and cfg.reliability.protection_sweep_enabled is False
