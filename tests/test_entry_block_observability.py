"""Review 7 Nov: entry blocks alert at once, de-duplicate, announce recovery,
and record the exact reason against each would-be entry."""
from collections import Counter
from types import SimpleNamespace

from bot.reliability.alerts import AlertDeduper
from bot.trading.strategy import CycleStats, SentimentStrategy


class Clock:
    t = 1_000_000.0

    def __call__(self):
        return self.t


def test_first_block_alerts_immediately_dedups_then_recovers():
    sent, clock = [], Clock()
    s = SentimentStrategy.__new__(SentimentStrategy)
    s.alerts = AlertDeduper(lambda t, sev: sent.append((sev, t)), clock=clock)
    s._alert_entry_blocks({"entry_block:ProtectionManager": "AAPL protection status UNKNOWN"})
    s._alert_entry_blocks({"entry_block:ProtectionManager": "AAPL protection status UNKNOWN"})
    assert len(sent) == 1 and sent[0][0] == "critical" and "NEW ENTRIES BLOCKED" in sent[0][1]
    clock.t += 3601
    s._alert_entry_blocks({"entry_block:ProtectionManager": "AAPL protection status UNKNOWN"})
    assert len(sent) == 2                                  # hourly reminder
    s._alert_entry_blocks({})
    assert "no longer blocked" in sent[-1][1]
    s._alert_entry_blocks({})
    assert len(sent) == 3                                  # recovery sent once


def strategy_for_buy_path(score):
    s = SentimentStrategy.__new__(SentimentStrategy)
    decisions = []
    sent = SimpleNamespace(score=score, label="positive", article_count=9, positive_count=4,
                           negative_count=0, rationale="r")
    s.cfg = SimpleNamespace(news=SimpleNamespace(lookback_hours=12, max_articles_per_symbol=15),
                            strategy=SimpleNamespace(reversion_enabled=False, reversion_live=False,
                                                     buy_threshold=8.0))
    s.news = SimpleNamespace(fetch=lambda *a: [])
    s.analyzer = SimpleNamespace(analyze=lambda sym, arts: sent)
    s.recorder = SimpleNamespace(record_decision=lambda **k: decisions.append(k))
    s._entry_block_reason = "AAPL protection status UNKNOWN"
    return s, decisions


def test_blocked_would_be_entry_records_exact_reason():
    s, decisions = strategy_for_buy_path(9.0)
    s._process_symbol("MSFT", {}, 0.0, {}, set(), Counter(), False, CycleStats())
    blocked = [d for d in decisions if d["decision"] == "buy_blocked"]
    assert len(blocked) == 1 and blocked[0]["reason"] == "AAPL protection status UNKNOWN"


def test_low_score_symbols_are_not_recorded_as_blocked_entries():
    s, decisions = strategy_for_buy_path(3.0)
    s._process_symbol("MSFT", {}, 0.0, {}, set(), Counter(), False, CycleStats())
    assert not any(d["decision"] == "buy_blocked" for d in decisions)
