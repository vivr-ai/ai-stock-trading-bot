"""Stage 6b: strategy routes decided exits to the exit manager only when it is
enabled, and books the ledger from the actual fill."""
from types import SimpleNamespace

from bot.reliability import exit_manager as em
from bot.trading.strategy import SentimentStrategy

SENT = SimpleNamespace(score=-6.0, label="negative", article_count=4, rationale="r",
                       positive_count=0, negative_count=3)


class Rec:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, k))


def make(state=None):
    s = SentimentStrategy.__new__(SentimentStrategy)
    s.cfg = SimpleNamespace(risk=SimpleNamespace(dry_run=False))
    s.recorder = Rec()
    s.broker = SimpleNamespace(env_tag="s", close_position=lambda sym: (_ for _ in ()).throw(
        AssertionError("close_position must not run when the exit manager is on")))
    s.exit_manager = None if state is None else SimpleNamespace(request=lambda sym, reason: state)
    s._protected_this_cycle = set()
    return s


def test_exit_manager_off_uses_close_position():
    s = make()
    s.broker.close_position = lambda sym: None
    s.broker.last_order_problem = "x"
    s.protection = None
    assert s._do_sell("AAPL", SENT, "sentiment") is False      # today's path ran


def test_underway_states_count_as_sold_others_do_not():
    for state, expected in [(em.REPRICING, True), (em.EXIT_WORKING, True), (em.RECONCILED, True),
                            (em.EXIT_REQUESTED, False), (em.ESCALATED, False), (em.UNCERTAIN, False)]:
        s = make(state)
        assert s._do_sell("AAPL", SENT, "sentiment") is expected, state
        assert any(n == "record_decision" and k["decision"] == "sell_requested" for n, k in s.recorder.calls)


def test_managed_exit_ledger_uses_fill_price_and_skips_fills_that_are_not_ours():
    s = make(em.REPRICING)
    logged = []
    s.closed_trade_logger = SimpleNamespace(log=lambda *a, **k: logged.append(a) or -5.0)
    s.state = SimpleNamespace(pop_open=lambda sym: {"qty": 10, "entry_price": 100.0, "entry_time": 1.0})
    s._record_managed_exit("AAPL", "sentiment", {"client_order_id": "alpaca-stop-leg",
                                                 "filled_avg_price": 90.0, "filled_qty": 10})
    assert logged == [] and not s.recorder.calls                # stop filled: auto-exit path logs it
    s._record_managed_exit("AAPL", "sentiment", {"client_order_id": "sR-AAPL-abc-1", "id": "o9",
                                                 "filled_avg_price": 99.5, "filled_qty": 10})
    assert logged[0][3] == 99.5
    closed = [k for n, k in s.recorder.calls if n == "record_closed_trade"][0]
    assert closed["exit_price"] == 99.5 and closed["exit_reason"] == "sentiment"
