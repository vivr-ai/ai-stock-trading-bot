"""Limitation 14, integration level (decision Q, 25 Nov 2026): a full
run_cycle with the SPY market data missing or invalid must block NEW entries
only. The lease check, protection sweep, pending-fill sync, legacy adoption,
exit detection and existing-position exits all still run, and entries resume
on the first cycle with valid data."""
from types import SimpleNamespace

import pytest

from bot.config import load_config
from bot.sentiment.base import SentimentResult
from bot.trading.alpaca_client import MarketSnapshot
from bot.trading.strategy import SentimentStrategy

NAN = float("nan")


class _Calls(list):
    def hit(self, name, *a, **k):
        self.append((name, a, k))
        return None


class _Recorder:
    def __init__(self):
        self.decisions = []

    def record_decision(self, **kw):
        self.decisions.append(kw)

    def __getattr__(self, _name):
        return lambda *a, **k: None


class _Broker:
    def __init__(self, spy_change, spy_snap, calls):
        self.spy_change, self.spy_snap, self.calls = spy_change, spy_snap, calls

    def is_market_open(self):
        return True

    def account_snapshot(self):
        return {"portfolio_value": 100000.0, "cash": 99950.0, "equity": 100000.0,
                "buying_power": 99950.0, "last_equity": 100000.0, "daytrade_count": 0,
                "pattern_day_trader": False}

    def open_positions(self):
        return {"VZ": 1.0}

    def pending_order_symbols(self):
        return set()

    def total_exposure(self):
        return 45.0

    def market_change_pct(self, symbol):
        self.calls.hit("market_change_pct", symbol)
        return self.spy_change

    def market_snapshot(self, symbol, **kw):
        self.calls.hit("market_snapshot", symbol)
        if symbol == "SPY":
            return self.spy_snap
        return MarketSnapshot(symbol, 50.0, 49.0, 2.0, 45.0, 1000.0, 2000.0, 2.0, history_n=22)

    def __getattr__(self, name):
        # Any order placement here would be an entry slipping through.
        if name.startswith(("submit", "place", "buy")):
            self.calls.hit("ORDER", name)    # recorded: run_cycle swallows per-symbol errors
            raise AssertionError(f"broker.{name} called")
        raise AttributeError(name)


def _strategy(monkeypatch, spy_change, spy_snap):
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test")
    calls = _Calls()
    s = SentimentStrategy.__new__(SentimentStrategy)
    s.cfg = load_config()
    s.broker = _Broker(spy_change, spy_snap, calls)
    s.recorder = _Recorder()
    s.lease = SimpleNamespace(ensure_ready=lambda: (calls.hit("lease.ensure_ready") or True, ""))
    s.protection = SimpleNamespace(sweep=lambda: calls.hit("protection.sweep"))
    s.db_gate = None
    s.entry_blockers = ()
    s.state = SimpleNamespace(detect_exits=lambda syms: calls.hit("detect_exits", syms) or [],
                              mark_exit=lambda sym: calls.hit("mark_exit", sym),
                              in_cooldown=lambda *a: False)
    s.bot_control_provider = SimpleNamespace(is_paused=lambda: (False, None))
    s._last_known_paused = False
    s._daily_loss_notified_date = None
    s.risk = SimpleNamespace(daily_loss_breached=lambda *a: False)
    s.universe = SimpleNamespace(get_universe=lambda n: ["VZ", "KO"])
    s.news = SimpleNamespace(fetch=lambda *a: [])
    # VZ (held): a severe negative reading, so the live sentiment exit fires.
    # KO (not held): a strong positive reading that would want to buy.
    s.analyzer = SimpleNamespace(analyze=lambda sym, arts: SentimentResult(
        symbol=sym, score=-9.0 if sym == "VZ" else 10.0,
        label="negative" if sym == "VZ" else "positive", rationale="t", article_count=10))
    s.summary_logger = SimpleNamespace(log_cycle=lambda **k: calls.hit("log_cycle"))
    for name in ("_maybe_notify_clock_degraded", "_maybe_notify_pdt", "_sync_open_positions_snapshot",
                 "_maybe_notify_shadow_verdict_ready", "_alert_entry_blocks"):
        setattr(s, name, (lambda n: lambda *a, **k: calls.hit(n))(name))
    s._sync_pending_entry_fills = lambda: calls.hit("sync_pending_entry_fills")
    s._adopt_legacy_positions = lambda: calls.hit("adopt_legacy_positions")
    s._maybe_trailing_stop_exit = lambda *a: False
    s._resolve_lot = lambda sym: {"entry_path": "sentiment_momentum"}
    s._do_sell = lambda sym, sent, reason, entry_path="sentiment_momentum": calls.hit("do_sell", sym) or True
    seen = []
    real = SentimentStrategy._process_symbol

    def spy_process(symbol, acct, exposure, open_positions, pending, sector_counts,
                    new_entries_allowed, stats, market_regime=None):
        seen.append((symbol, new_entries_allowed))
        return real(s, symbol, acct, exposure, open_positions, pending, sector_counts,
                    new_entries_allowed, stats, market_regime)
    s._process_symbol = spy_process
    return s, calls, seen


def _names(calls):
    return [c[0] for c in calls]


GOOD_SPY = MarketSnapshot("SPY", 760.0, 757.0, 0.4, 750.0, 1.0e6, 1.0e6, 1.0, history_n=52)


@pytest.mark.parametrize("change,snap,reason", [
    (None, GOOD_SPY, "market_change_unavailable"),
    (NAN, GOOD_SPY, "market_change_unavailable"),
    (0.4, None, "regime_data_unavailable"),
    (0.4, MarketSnapshot("SPY", 760.0, 757.0, 0.4, NAN, 1.0e6, 1.0e6, 1.0, history_n=52),
     "regime_data_unavailable"),
    (0.4, MarketSnapshot("SPY", 760.0, 757.0, 0.4, 750.0, 1.0e6, 1.0e6, 1.0, history_n=12),
     "regime_history_insufficient"),
])
def test_missing_spy_data_blocks_entries_but_not_position_management(monkeypatch, change, snap, reason):
    s, calls, seen = _strategy(monkeypatch, change, snap)
    s.run_cycle()
    names = _names(calls)
    # Everything that manages existing risk still ran, in a normal cycle.
    for required in ("lease.ensure_ready", "protection.sweep", "sync_pending_entry_fills",
                     "adopt_legacy_positions", "detect_exits", "log_cycle"):
        assert required in names, required
    assert s.last_cycle_outcome == "ok"
    # New entries blocked, with the fail-closed reason recorded ...
    assert [d["reason"] for d in s.recorder.decisions if d.get("decision") == "block_new_entries"] == [reason]
    assert seen == [("VZ", False), ("KO", False)]
    # ... the held position's exit still fired ...
    assert ("do_sell", ("VZ",), {}) in calls
    # ... and the would-be KO entry was recorded as blocked, never bought.
    assert "ORDER" not in names
    assert any(d.get("decision") == "buy_blocked" and d.get("symbol") == "KO" and d.get("reason") == reason
               for d in s.recorder.decisions)


def test_entries_resume_on_the_next_cycle_with_valid_data(monkeypatch):
    s, calls, seen = _strategy(monkeypatch, None, GOOD_SPY)
    s.run_cycle()
    assert seen[-1] == ("KO", False)
    s.broker.spy_change = 0.4                              # data is back
    seen.clear()
    s.recorder.decisions.clear()
    s.run_cycle()
    assert seen == [("VZ", True), ("KO", True)]
    assert not [d for d in s.recorder.decisions if d.get("decision") == "block_new_entries"]
