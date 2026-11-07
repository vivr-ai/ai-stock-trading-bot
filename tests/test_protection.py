"""Reliability v3 2H: three-state broker reads and protection restoration."""
from types import SimpleNamespace

import pytest

from bot.reliability.protection import (PROTECTED, UNDER_PROTECTED, UNKNOWN, UNPROTECTED,
                                        ProtectionManager, attempts_at_broker, classify,
                                        position_ref)
from bot.trading.alpaca_client import (BrokerReadError, BrokerSnapshot, OrderBlockedError,
                                       classify_submit_error, flatten_orders)


class HttpErr(Exception):
    def __init__(self, code, msg="err"):
        super().__init__(msg)
        self.status_code = code


RISK = SimpleNamespace(stop_loss_pct=10.0, take_profit_pct=20.0, trailing_stop_enabled=True,
                       trailing_stop_backstop_take_profit_pct=50.0)


def stop_leg(sym, qty, status="held", filled=0.0, cid="", role="stop_loss"):
    return {"id": f"o-{sym}-{len(cid)}", "client_order_id": cid, "symbol": sym, "side": "sell",
            "type": "stop", "status": status, "qty": qty, "filled_qty": filled,
            "group_role": role, "order_class": "bracket"}


class FakeBroker:
    def __init__(self):
        self.positions = {"AAPL": {"qty": 10.0, "qty_available": 10.0, "avg_entry_price": 100.0,
                                   "side": "long"}}
        self.orders = [stop_leg("AAPL", 10)]
        self.positions_ok = self.orders_ok = self.history_ok = True
        self.submits = []
        self.submit_behaviour = []      # queue of: "ok" | Exception
        self.price = 105.0
        self.lookup = {}                # cid -> order dict | None (not found) | "unknown"
        self.history = []

    def positions_snapshot(self):
        return BrokerSnapshot(self.positions_ok, dict(self.positions) if self.positions_ok else None,
                              "" if self.positions_ok else "timeout")

    def open_orders_snapshot(self):
        return BrokerSnapshot(self.orders_ok, list(self.orders) if self.orders_ok else None,
                              "" if self.orders_ok else "HTTP 503")

    def symbol_orders_snapshot(self, sym, after_iso=None):
        return BrokerSnapshot(self.history_ok, list(self.history) if self.history_ok else None,
                              "" if self.history_ok else "timeout")

    def latest_price(self, sym):
        return self.price

    def last_fill(self, sym, side=None):
        return SimpleNamespace(order_id="3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a8b90")

    def order_by_client_id(self, cid):
        v = self.lookup.get(cid)
        if v == "unknown":
            return BrokerSnapshot(False, None, "timeout")
        return BrokerSnapshot(True, v, "")

    def submit_oco_once(self, sym, qty, stop, tp, cid):
        self.submits.append((sym, qty, stop, tp, cid))
        b = self.submit_behaviour.pop(0) if self.submit_behaviour else "ok"
        if isinstance(b, Exception):
            raise b
        if b == "ok":
            self.history.append({"client_order_id": cid, "status": "new", "symbol": sym})
        return {"id": "new", "client_order_id": cid}


class Alerts:
    def __init__(self):
        self.raised, self.resolved = [], []

    def raise_(self, key, text, severity="warning", repeat_s=None):
        self.raised.append((key, severity, text))
        return True

    def resolve(self, key, text=None, severity="warning"):
        self.resolved.append(key)


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class HB:
    def __init__(self):
        self.fails = []

    def fail(self, why):
        self.fails.append(why)


def mgr(broker=None, **kw):
    b, a, c, hb = broker or FakeBroker(), Alerts(), Clock(), HB()
    m = ProtectionManager(b, RISK, "s", a, heartbeat=hb, clock=c, **kw)
    return m, b, a, c, hb


def keys(alerts, sev=None):
    return [k for k, s, _ in alerts.raised if sev is None or s == sev]


# ---- reads & classification ----------------------------------------------------------
def test_flatten_keeps_group_fields_and_parent():
    rows = [{"id": "p", "symbol": "AAPL", "side": "sell", "type": "limit", "status": "new",
             "qty": "10", "group_id": "g1", "group_role": "take_profit",
             "legs": [{"id": "l", "symbol": "AAPL", "side": "sell", "type": "stop", "status": "held",
                       "qty": "10", "group_id": "g1", "group_role": "stop_loss"}]}]
    flat = flatten_orders(rows)
    assert [o["id"] for o in flat] == ["p", "l"]
    assert flat[1]["parent_id"] == "p" and flat[1]["group_role"] == "stop_loss"
    assert flat[1]["qty"] == 10.0


def test_classify_states():
    pos = {"A": {"qty": 10}, "B": {"qty": 10}, "C": {"qty": 10}, "D": {"qty": 5, "side": "short"}}
    orders = [stop_leg("A", 10), stop_leg("B", 4), stop_leg("C", 10, status="canceled")]
    st = classify(pos, orders)
    assert st["A"][0] == PROTECTED and st["B"] == (UNDER_PROTECTED, 4)
    assert st["C"][0] == UNPROTECTED and st["D"][0] == UNKNOWN
    assert all(v[0] == UNKNOWN for v in classify(pos, None).values())   # orders unreadable


def test_partially_filled_stop_covers_only_remaining():
    st = classify({"A": {"qty": 7}}, [stop_leg("A", 10, status="partially_filled", filled=3)])
    assert st["A"][0] == PROTECTED
    st = classify({"A": {"qty": 7}}, [stop_leg("A", 10, status="partially_filled", filled=5)])
    assert st["A"] == (UNDER_PROTECTED, 5)


def test_submit_error_classification():
    assert classify_submit_error(HttpErr(422)) == "rejected"
    assert classify_submit_error(HttpErr(403)) == "rejected"
    assert classify_submit_error(HttpErr(500)) == "uncertain"
    assert classify_submit_error(HttpErr(429)) == "uncertain"
    assert classify_submit_error(TimeoutError("read timed out")) == "uncertain"


def test_ids_are_deterministic():
    p = {"avg_entry_price": 100.0, "qty": 10}
    assert position_ref("3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a8b90", p) == "3f2a9c1e5b7d"
    assert position_ref(None, p) == position_ref(None, dict(p)) == "u10000q10"
    orders = [{"client_order_id": "sP-AAPL-3f2a9c1e5b7d-1", "status": "canceled"},
              {"client_order_id": "sP-AAPL-3f2a9c1e5b7d-2", "status": "rejected"},
              {"client_order_id": "sP-MSFT-3f2a9c1e5b7d-9", "status": "new"}]
    assert attempts_at_broker(orders, "sP-AAPL-3f2a9c1e5b7d-") == (2, False)


def test_pending_orders_fail_closed():
    from bot.trading.alpaca_client import AlpacaBroker

    br = AlpacaBroker.__new__(AlpacaBroker)
    br._retry = lambda fn, name: (_ for _ in ()).throw(RuntimeError("503"))
    br._trading = None
    with pytest.raises(BrokerReadError):
        br.pending_order_symbols()


# ---- behaviour -------------------------------------------------------------------------
def test_protected_position_needs_nothing():
    m, b, a, c, _ = mgr()
    assert m.sweep() == {"AAPL": PROTECTED}
    assert b.submits == [] and m.entry_blocker() == (False, "")


def test_missing_protection_restored_immediately_with_deterministic_id():
    m, b, a, c, _ = mgr()
    b.orders = []
    m.sweep()
    assert len(b.submits) == 1
    sym, qty, stop, tp, cid = b.submits[0]
    assert (sym, qty, stop, tp, cid) == ("AAPL", 10.0, 90.0, 150.0, "sP-AAPL-3f2a9c1e5b7d-1")
    assert ("prot_missing:AAPL", "warning") in [(k, s) for k, s, _ in a.raised]
    b.orders = [stop_leg("AAPL", 10, cid="sP-AAPL-3f2a9c1e5b7d-1")]   # broker now shows it
    c.t += 30
    assert m.sweep() == {"AAPL": PROTECTED}
    assert len(b.submits) == 1 and "prot_missing:AAPL" in a.resolved


def test_rejections_retry_at_60_and_120_then_escalate():
    m, b, a, c, hb = mgr()
    b.orders = []
    b.submit_behaviour = [HttpErr(422, "insufficient qty")] * 3
    m.sweep()                                    # attempt 1 (t=0)
    assert len(b.submits) == 1 and "prot_failed:AAPL" in keys(a, "critical")
    c.t += 30; m.sweep()
    assert len(b.submits) == 1                   # not before 60 s
    c.t += 30; m.sweep()                         # t=60: attempt 2
    c.t += 60; m.sweep()                         # t=120: attempt 3
    assert [s[4][-1] for s in b.submits] == ["1", "2", "3"]
    c.t += 15; m.sweep()
    assert "prot_manual:AAPL" in keys(a, "critical") and hb.fails
    c.t += 600; m.sweep()
    assert len(b.submits) == 3                   # never a 4th order


def test_submitted_but_never_confirmed_counts_as_failure():
    m, b, a, c, _ = mgr()
    b.orders = []
    m.sweep()                                    # accepted, but never shows up as a stop
    b.history = [{"client_order_id": "sP-AAPL-3f2a9c1e5b7d-1", "status": "canceled"}]
    c.t += 31; m.sweep()
    assert "prot_failed:AAPL" in keys(a, "critical")
    c.t += 30; m.sweep()                         # t=61: attempt 2 with the next number
    assert b.submits[-1][4] == "sP-AAPL-3f2a9c1e5b7d-2"


def test_uncertain_submit_that_exists_is_adopted_not_duplicated():
    m, b, a, c, _ = mgr()
    b.orders = []
    b.submit_behaviour = [TimeoutError("read timed out")]
    m.sweep()
    cid = b.submits[0][4]
    b.lookup[cid] = {"status": "new", "client_order_id": cid}
    c.t += 15; m.sweep()
    assert len(b.submits) == 1
    b.orders = [stop_leg("AAPL", 10, cid=cid)]
    c.t += 30
    assert m.sweep() == {"AAPL": PROTECTED} and len(b.submits) == 1


def test_uncertain_submit_not_found_resubmits_same_id_after_60s():
    m, b, a, c, _ = mgr()
    b.orders = []
    b.submit_behaviour = [HttpErr(504)]
    m.sweep()
    cid = b.submits[0][4]
    b.lookup[cid] = None                         # Alpaca: not found
    c.t += 30; m.sweep()
    assert len(b.submits) == 1                   # too early to conclude
    c.t += 31; m.sweep()
    assert len(b.submits) == 2 and b.submits[1][4] == cid    # SAME id


def test_uncertain_and_lookup_unknown_waits():
    m, b, a, c, _ = mgr()
    b.orders = []
    b.submit_behaviour = [HttpErr(500)]
    m.sweep()
    b.lookup[b.submits[0][4]] = "unknown"
    for _ in range(10):
        c.t += 60; m.sweep()
    assert len(b.submits) == 1                   # never a blind retry


def test_unknown_orders_place_nothing_and_block_entries():
    m, b, a, c, _ = mgr()
    m.sweep()                                    # protected first
    b.orders_ok = False
    c.t += 300
    assert m.sweep() == {"AAPL": UNKNOWN}
    assert b.submits == [] and m.entry_blocker()[0]
    assert "prot_unknown:AAPL" not in keys(a)    # recently protected: quiet for 10 min
    c.t += 600; m.sweep()
    assert "prot_unknown:AAPL" in keys(a, "warning")
    c.t += 1200; m.sweep()
    assert "prot_unknown:AAPL" in keys(a, "critical")
    assert b.submits == []


def test_unknown_without_recent_confirmation_is_critical_at_once():
    m, b, a, c, _ = mgr()
    b.orders_ok = False
    m.sweep()
    assert "prot_unknown:AAPL" in keys(a, "critical") and b.submits == []


def test_positions_unreadable_is_global_unknown():
    m, b, a, c, _ = mgr()
    b.positions_ok = False
    assert m.sweep() == {}
    assert "prot_global" in keys(a, "critical") and m.entry_blocker()[0] and b.submits == []


def test_under_protected_restores_only_the_gap():
    m, b, a, c, _ = mgr()
    b.orders = [stop_leg("AAPL", 6)]
    b.positions["AAPL"]["qty_available"] = 4.0
    m.sweep()
    assert b.submits[0][1] == 4.0


def test_shares_held_by_other_order_are_not_double_sold():
    m, b, a, c, _ = mgr()
    b.orders = []
    b.positions["AAPL"]["qty_available"] = 0.0   # e.g. a working sell holds them
    m.sweep()
    assert b.submits == [] and "prot_failed:AAPL" in keys(a, "critical")


def test_price_below_stop_escalates_without_order():
    m, b, a, c, hb = mgr()
    b.orders = []
    b.price = 89.0
    m.sweep()
    assert b.submits == [] and "prot_manual:AAPL" in keys(a, "critical") and hb.fails


def test_previous_instance_order_still_working_is_not_duplicated():
    m, b, a, c, _ = mgr()
    b.orders = []
    b.history = [{"client_order_id": "sP-AAPL-3f2a9c1e5b7d-1", "status": "pending_new"}]
    m.sweep()
    assert b.submits == []


def test_entries_blocked_after_five_minutes_unprotected():
    m, b, a, c, _ = mgr()
    b.orders = []
    b.submit_behaviour = [HttpErr(422)] * 3
    m.sweep()
    assert m.entry_blocker() == (False, "")
    c.t += 301; m.sweep()
    assert m.entry_blocker()[0]


def test_lease_block_is_not_counted_as_attempt():
    m, b, a, c, _ = mgr()
    b.orders = []
    b.submit_behaviour = [OrderBlockedError("lease")]
    m.sweep()
    assert m.tracks["AAPL"].attempts == []


def test_closed_position_clears_tracking():
    m, b, a, c, _ = mgr()
    b.orders = []
    b.submit_behaviour = [HttpErr(422)]
    m.sweep()
    b.positions = {}
    m.sweep()
    assert m.tracks == {} and "prot_missing:AAPL" in a.resolved


def test_stale_status_blocks_entries_and_standby_does_not_sweep():
    m, b, a, c, _ = mgr()
    m.sweep()
    c.t += 901
    assert m.entry_blocker()[0]
    m.active = lambda: False
    m.next_sweep_at = 0
    m.tick()
    assert m.last_ok_sweep == c.t - 901           # no sweep happened


def test_adoption_defers_protection_to_manager():
    from bot.trading.strategy import SentimentStrategy

    calls = []
    s = SentimentStrategy.__new__(SentimentStrategy)
    pos = SimpleNamespace(avg_entry_price=100.0, qty=10.0)
    s.broker = SimpleNamespace(
        open_positions_detailed=lambda: {"AAPL": pos},
        get_protected_symbols=lambda: set(),
        submit_protective_exit=lambda *a, **k: calls.append(a),
    )
    s.recorder = SimpleNamespace(get_last_buy_trade=lambda sym: {"price": 100.0},
                                 record_trade=lambda **k: None, record_notification=lambda **k: None)
    s.cfg = SimpleNamespace(risk=SimpleNamespace(stop_loss_pct=10.0, take_profit_pct=20.0,
                                                 trailing_stop_enabled=True, dry_run=False,
                                                 trailing_stop_backstop_take_profit_pct=50.0))
    s.protection = object()
    s._adopt_legacy_positions()
    assert calls == []


# ---- emergency exit below the stop level (review 7 Nov) ------------------------------------
class EBroker(FakeBroker):
    def __init__(self):
        super().__init__()
        self.orders = []
        self.price = 85.0                         # stop level is 90
        self.guard = (True, "lease held")
        self.exits = []
        self.exit_behaviour = "ok"
        self.order_guard = lambda kind: self.guard
        self.last_order_problem = None

    def _next_exit_client_id(self, kind, sym, pos):
        return f"s{kind}-{sym}-3f2a9c1e5b7d-1"

    def submit_order_once(self, body, kind, what):
        self.exits.append(body)
        if isinstance(self.exit_behaviour, Exception):
            raise self.exit_behaviour
        return {"id": "x1", "status": "accepted"}


def emgr(enabled=True):
    m, b, a, c, hb = mgr(EBroker())
    m.emergency_exit_enabled = enabled
    return m, b, a, c, hb


def test_emergency_exit_closes_uncovered_quantity_once():
    m, b, a, c, _ = emgr()
    m.sweep()
    assert len(b.exits) == 1 and b.submits == []          # no stop placed below the market
    e = b.exits[0]
    assert (e["side"], e["type"], e["qty"], e["client_order_id"]) == ("sell", "market", "10.0",
                                                                       "sX-AAPL-3f2a9c1e5b7d-1")
    assert "prot_emergency:AAPL" in keys(a, "critical")
    c.t += 15; m.sweep()
    assert len(b.exits) == 1                               # never a second sell
    b.positions = {}
    c.t += 15; m.sweep()
    assert m.tracks == {} and "prot_emergency:AAPL" in a.resolved


def test_emergency_exit_disabled_escalates_only():
    m, b, a, c, hb = emgr(enabled=False)
    m.sweep()
    assert b.exits == [] and "prot_manual:AAPL" in keys(a, "critical") and hb.fails


def test_emergency_exit_requires_fresh_lease():
    m, b, a, c, _ = emgr()
    b.guard = (False, "trading lease not confirmed for 75s")
    m.sweep()
    assert b.exits == [] and "prot_manual:AAPL" in keys(a, "critical")


def test_emergency_exit_never_duplicates_a_working_sell():
    m, b, a, c, _ = emgr()
    b.orders = [{"id": "tp", "client_order_id": "sX-AAPL-old-1", "symbol": "AAPL", "side": "sell",
                 "type": "market", "status": "accepted", "qty": 10, "filled_qty": 0}]
    m.sweep()
    assert b.exits == [] and "prot_manual:AAPL" in keys(a, "critical")


def test_emergency_exit_needs_available_shares():
    m, b, a, c, _ = emgr()
    b.positions["AAPL"]["qty_available"] = 0.0
    m.sweep()
    assert b.exits == []


def test_emergency_exit_unknown_outcome_escalates_without_retry():
    from bot.trading.alpaca_client import OrderOutcomeUnknown

    m, b, a, c, _ = emgr()
    b.exit_behaviour = OrderOutcomeUnknown("response lost; NOT resubmitted")
    m.sweep()
    c.t += 120; m.sweep()
    assert len(b.exits) == 1 and "prot_manual:AAPL" in keys(a, "critical")


def test_emergency_exit_still_open_after_60s_escalates():
    m, b, a, c, _ = emgr()
    m.sweep()
    c.t += 61; m.sweep()
    assert len(b.exits) == 1 and "prot_manual:AAPL" in keys(a, "critical")


def test_unknown_state_never_reaches_emergency_exit():
    m, b, a, c, _ = emgr()
    b.orders_ok = False
    m.sweep()
    assert b.exits == [] and b.submits == []


def test_under_protected_below_stop_with_working_stop_escalates_instead_of_selling():
    """A stop leg is still working for 6 shares below its own level (it should
    be triggering). Selling the other 4 next to it is not attempted: any
    working sell on the symbol means escalate, never add another sell."""
    m, b, a, c, _ = emgr()
    b.orders = [stop_leg("AAPL", 6)]
    b.positions["AAPL"]["qty_available"] = 4.0
    m.sweep()
    assert b.exits == [] and "prot_manual:AAPL" in keys(a, "critical")
