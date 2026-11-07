"""Stage 6b part 1: every order is created by exactly one POST; ambiguous
outcomes are resolved by lookup, never by resubmission."""
from types import SimpleNamespace

import pytest

import bot.trading.alpaca_client as ac
from bot.trading.alpaca_client import (AlpacaBroker, BrokerSnapshot, OrderOutcomeUnknown,
                                       OrderRejected)


class HttpErr(Exception):
    def __init__(self, code, msg="err"):
        super().__init__(msg)
        self.status_code = code


def broker(responses, lookups=None):
    b = AlpacaBroker.__new__(AlpacaBroker)
    b.allow_submit, b.mode, b.order_guard, b.env_tag = True, "paper", None, "s"
    b.posts, b.lookups = [], []
    queue = list(responses)

    def raw(method, path, data=None, retry=True):
        assert method == "POST" and retry is False      # never an automatic retry
        b.posts.append(data)
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    look = list(lookups or [])

    def by_cid(cid):
        b.lookups.append(cid)
        return look.pop(0) if look else BrokerSnapshot(True, None, "not found")

    b._raw, b.order_by_client_id = raw, by_cid
    return b


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(ac.time, "sleep", lambda s: None)


BODY = {"symbol": "AAPL", "qty": "1", "side": "sell", "type": "market", "client_order_id": "sX-AAPL-r-1"}


def test_accepted():
    b = broker([{"id": "o1", "status": "accepted"}])
    assert b.submit_order_once(dict(BODY), "exit", "x")["id"] == "o1" and len(b.posts) == 1


def test_definite_rejection_places_nothing_and_does_not_retry():
    b = broker([HttpErr(422, "qty must be > 0")])
    with pytest.raises(OrderRejected):
        b.submit_order_once(dict(BODY), "exit", "x")
    assert len(b.posts) == 1 and b.lookups == []


def test_duplicate_client_id_returns_the_existing_order():
    b = broker([HttpErr(422, '{"message":"client_order_id must be unique"}')],
               [BrokerSnapshot(True, {"id": "existing", "status": "new"})])
    assert b.submit_order_once(dict(BODY), "exit", "x")["id"] == "existing" and len(b.posts) == 1


def test_ambiguous_then_found_by_lookup():
    b = broker([TimeoutError("read timed out")],
               [BrokerSnapshot(True, None, "not found"), BrokerSnapshot(True, {"id": "late", "status": "new"})])
    assert b.submit_order_once(dict(BODY), "exit", "x")["id"] == "late"
    assert len(b.posts) == 1 and len(b.lookups) == 2


@pytest.mark.parametrize("err", [TimeoutError("timeout"), HttpErr(500), HttpErr(504), HttpErr(429)])
def test_ambiguous_not_found_is_unknown_and_never_resubmitted(err):
    b = broker([err])
    with pytest.raises(OrderOutcomeUnknown, match="NOT resubmitted"):
        b.submit_order_once(dict(BODY), "exit", "x")
    assert len(b.posts) == 1 and len(b.lookups) == 4


def test_ambiguous_with_failing_lookups_is_unknown():
    b = broker([ConnectionError("reset")], [BrokerSnapshot(False, None, "503")] * 4)
    with pytest.raises(OrderOutcomeUnknown, match="lookup failed"):
        b.submit_order_once(dict(BODY), "exit", "x")
    assert len(b.posts) == 1


def test_buy_and_protection_go_through_single_attempt():
    b = broker([{"id": "b1", "status": "accepted"}, {"id": "p1", "status": "new"}])
    o = b.submit_bracket_buy("AAPL", 3, 90.0, 150.0, client_order_id="AAPL-2026110913")
    assert o.order_id == "b1" and b.posts[0]["order_class"] == "bracket"
    assert b.posts[0]["client_order_id"] == "AAPL-2026110913"
    o = b.submit_protective_exit("AAPL", 3, 90.0, 150.0, client_order_id="sP-AAPL-r-1")
    assert o.order_id == "p1" and b.posts[1]["order_class"] == "oco"


def test_buy_outcome_unknown_propagates():
    b = broker([HttpErr(503)])
    with pytest.raises(OrderOutcomeUnknown):
        b.submit_bracket_buy("AAPL", 3, 90.0, 150.0, client_order_id="AAPL-slot")
    assert len(b.posts) == 1


def close_broker(responses, history=None, positions_ok=True):
    b = broker(responses)
    b.cancel_orders_for = lambda sym, **k: None
    b.positions_snapshot = lambda: BrokerSnapshot(positions_ok, {"AAPL": {"qty": 5.0, "qty_available": 5.0,
                                                                            "avg_entry_price": 100.0}}
                                                  if positions_ok else None, "" if positions_ok else "503")
    b.last_fill = lambda sym, side=None: SimpleNamespace(order_id="0123456789abcdef")
    b.symbol_orders_snapshot = lambda sym, after_iso=None: BrokerSnapshot(history is not False, history or [])
    return b


def test_close_uses_deterministic_exit_id_and_next_attempt_number():
    b = close_broker([{"id": "x2", "status": "accepted"}],
                     history=[{"client_order_id": "sX-AAPL-0123456789ab-1", "status": "canceled"}])
    assert b.close_position("AAPL").order_id == "x2"
    assert b.posts[0]["client_order_id"] == "sX-AAPL-0123456789ab-2" and b.posts[0]["qty"] == "5.0"


def test_close_refuses_while_an_earlier_exit_is_still_working():
    b = close_broker([], history=[{"client_order_id": "sX-AAPL-0123456789ab-1", "status": "new"}])
    assert b.close_position("AAPL") is None and b.posts == []
    assert "still working" in b.last_order_problem


def test_close_refuses_when_position_or_history_unknown():
    b = close_broker([], positions_ok=False)
    assert b.close_position("AAPL") is None and b.posts == [] and "unknown" in b.last_order_problem
    b = close_broker([], history=False)
    assert b.close_position("AAPL") is None and b.posts == []


def test_close_outcome_unknown_is_reported_not_retried():
    b = close_broker([HttpErr(504)])
    assert b.close_position("AAPL") is None
    assert len(b.posts) == 1 and b.last_order_problem.startswith("OUTCOME UNKNOWN")


def test_sdk_rest_client_never_retries_posts():
    br = AlpacaBroker("k" * 20, "s" * 40, mode="paper", allow_submit=True)
    assert br._trading._retry == 0 and br._trading._retry_codes == []


def test_failed_sell_triggers_immediate_protection_sweep():
    from bot.trading.strategy import SentimentStrategy

    s = SentimentStrategy.__new__(SentimentStrategy)
    swept, notes = [], []
    s.cfg = SimpleNamespace(risk=SimpleNamespace(dry_run=False))
    s.broker = SimpleNamespace(close_position=lambda sym: None,
                               last_order_problem="OUTCOME UNKNOWN: lost response")
    s.recorder = SimpleNamespace(record_notification=lambda **k: notes.append(k))
    s.protection = SimpleNamespace(sweep=lambda: swept.append(1))
    sent = SimpleNamespace(score=-9, label="negative")
    assert s._do_sell("AAPL", sent, "sentiment") is False
    assert swept == [1] and notes[0]["severity"] == "critical"
