"""Stage 6b: exit state machine against a scripted fake Alpaca.

The fake follows what the real paper account did in Stage 6a (9-10 Nov):
PATCH replaces the take-profit (old -> 'replaced', new order with `replaces`,
same group, stop stays 'held'); qty can't change; a second PATCH on a
replaced ID is 422; cancelling one leg of a bracket/OCO cancels its sibling.
"Pass" is judged from the fake broker's positions, orders and fills, never
from log lines.
"""
from types import SimpleNamespace

import pytest

from bot.reliability import exit_manager as em
from bot.trading.alpaca_client import BrokerSnapshot, OrderOutcomeUnknown, OrderRejected


class HttpErr(Exception):
    def __init__(self, code, msg="err"):
        super().__init__(msg)
        self.status_code = code


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class FakeAlpaca:
    def __init__(self, qty=10.0, tp_price=120.0, stop_price=90.0):
        self.qty, self.held_for_orders = qty, qty
        self.n = 0
        self.orders = []
        self.sells_filled = []
        self.bid = 100.0
        self.quote = None              # override the quote (stale / halted / wide)
        self.read_ok = True
        self.replace_behaviour = []    # queue: "ok" | "reject" | "unknown" | "fill_first"
        self.cancel_behaviour = []     # queue: "ok" | "stuck"
        self.submit_behaviour = []     # queue: "ok" | "reject" | "unknown_exists" | "unknown_missing"
        self.calls = []
        g = "g1"
        self._add(side="buy", type="market", status="filled", qty=qty, role="entry", group=g,
                  cid="sP-entry", oid="abcdef1234567890", filled_qty=qty, avg=100.0)
        self.tp = self._add(side="sell", type="limit", status="new", qty=qty, role="take_profit",
                            group=g, limit=tp_price, oclass="bracket")
        self.stop = self._add(side="sell", type="stop", status="held", qty=qty, role="stop_loss",
                              group=g, stop=stop_price, oclass="bracket")

    def _add(self, **k):
        self.n += 1
        o = {"id": k.get("oid") or f"o{self.n}", "client_order_id": k.get("cid") or f"alpaca-{self.n}",
             "symbol": "AAPL", "side": k["side"], "type": k["type"], "status": k["status"],
             "qty": k["qty"], "filled_qty": k.get("filled_qty", 0.0), "limit_price": k.get("limit"),
             "stop_price": k.get("stop"), "group_id": k.get("group"), "group_role": k.get("role"),
             "order_class": k.get("oclass", ""), "replaces": k.get("replaces"), "replaced_by": None,
             "filled_avg_price": k.get("avg"), "n": self.n}
        self.orders.append(o)
        return o

    def by_id(self, oid):
        return next(o for o in self.orders if o["id"] == oid)

    # --- reads ---
    def positions_snapshot(self):
        if not self.read_ok:
            return BrokerSnapshot(False, None, "timeout")
        if self.qty <= 0:
            return BrokerSnapshot(True, {}, "")
        working = sum(o["qty"] - o["filled_qty"] for o in self.orders
                      if o["side"] == "sell" and o["status"] in ("new", "partially_filled")
                      and o["type"] != "stop")
        held = max(working, self.held_for_orders if self._any_working() else 0.0)
        return BrokerSnapshot(True, {"AAPL": {"qty": self.qty, "qty_available": max(0.0, self.qty - held),
                                              "avg_entry_price": 100.0, "side": "long"}}, "")

    def _any_working(self):
        return any(o["side"] == "sell" and o["status"] in ("new", "held", "partially_filled",
                                                           "pending_cancel", "pending_replace")
                   for o in self.orders)

    def symbol_orders_snapshot(self, sym, after_iso=None):
        if not self.read_ok:
            return BrokerSnapshot(False, None, "timeout")
        return BrokerSnapshot(True, [dict(o) for o in sorted(self.orders, key=lambda o: -o["n"])], "")

    def order_by_client_id(self, cid):
        o = next((o for o in self.orders if o["client_order_id"] == cid), None)
        return BrokerSnapshot(True, dict(o) if o else None, "")

    def latest_bid(self, sym):
        return self.bid

    def latest_quote(self, sym):
        if self.quote is not None:
            return dict(self.quote)
        return {"bid": self.bid, "ask": round(self.bid * 1.001, 2), "age_s": 1.0}

    # --- writes ---
    def replace_order_once(self, oid, price, cid, what):
        self.calls.append(("replace", oid, price, cid))
        b = self.replace_behaviour.pop(0) if self.replace_behaviour else "ok"
        old = self.by_id(oid)
        if old["status"] == "replaced":
            raise OrderRejected("order already replaced", 422)
        if b == "reject":
            raise OrderRejected("422 invalid price", 422)
        if b == "fill_first":           # the original TP fills before the replace lands
            self._fill(old, old["limit_price"])
            raise OrderRejected("order is filled", 422)
        new = self._add(side="sell", type="limit", status="new", qty=old["qty"], role="take_profit",
                        group=old["group_id"], limit=price, cid=cid, replaces=oid, oclass=old["order_class"])
        old["status"], old["replaced_by"] = "replaced", new["id"]
        if b == "unknown":
            raise OrderOutcomeUnknown("response lost")
        return dict(new)

    def cancel_order_once(self, oid, what):
        self.calls.append(("cancel", oid))
        b = self.cancel_behaviour.pop(0) if self.cancel_behaviour else "ok"
        o = self.by_id(oid)
        if o["status"] not in ("new", "held", "partially_filled"):
            return False
        if b == "stuck":
            o["status"] = "pending_cancel"
            return True
        o["status"] = "canceled"
        for sib in self.orders:   # OCO: cancelling one leg cancels its sibling
            if sib is not o and sib["group_id"] and sib["group_id"] == o["group_id"] \
                    and sib["side"] == "sell" and sib["status"] in ("new", "held"):
                sib["status"] = "canceled"
        if not self._any_working():
            self.held_for_orders = 0.0
        return True

    def submit_order_once(self, body, kind, what):
        self.calls.append(("submit", body["client_order_id"], body["qty"]))
        b = self.submit_behaviour.pop(0) if self.submit_behaviour else "ok"
        if b == "reject":
            raise OrderRejected("422 insufficient qty", 422)
        if b == "unknown_missing":
            raise OrderOutcomeUnknown("lost")
        o = self._add(side="sell", type="market", status="new", qty=float(body["qty"]),
                      cid=body["client_order_id"])
        if b == "unknown_exists":
            raise OrderOutcomeUnknown("lost")
        return dict(o)

    # --- market events (the test script drives these) ---
    def _fill(self, o, price, qty=None):
        q = o["qty"] - o["filled_qty"] if qty is None else qty
        o["filled_qty"] += q
        o["filled_avg_price"] = price
        o["status"] = "filled" if o["filled_qty"] >= o["qty"] else "partially_filled"
        self.qty -= q
        self.sells_filled.append((o["client_order_id"], q, price))
        if o["status"] == "filled":
            for sib in self.orders:
                if sib is not o and sib["group_id"] and sib["group_id"] == o["group_id"] \
                        and sib["side"] == "sell" and sib["status"] in ("new", "held"):
                    sib["status"] = "canceled"

    def fill_live_tp(self, price=99.9, qty=None):
        live = next(o for o in self.orders if o["group_role"] == "take_profit" and o["status"] in
                    ("new", "partially_filled"))
        self._fill(live, price, qty)

    def fill_x(self, price=99.5):
        x = next(o for o in self.orders if o["client_order_id"].startswith("sX-") and o["status"] == "new")
        self._fill(x, price)

    def working_stop(self):
        return [o for o in self.orders if o["type"] == "stop" and o["status"] in ("held", "new")]


class Alerts:
    def __init__(self):
        self.raised, self.resolved = [], []

    def raise_(self, key, text, severity="warning", repeat_s=None):
        self.raised.append((key, severity, text))
        return True

    def resolve(self, key, text=None, severity="warning"):
        self.resolved.append(key)
        return True


class Prot:
    def __init__(self):
        self.sweeps = 0

    def sweep(self):
        self.sweeps += 1
        return {}


def make(fake=None, lease=True, market=True):
    fake, alerts, clock, prot = fake or FakeAlpaca(), Alerts(), Clock(), Prot()
    journal, closed = [], []
    m = em.ExitManager(fake, alerts, "s", protection=prot, lease_ok=lambda: lease, clock=clock,
                       journal=lambda k, p: journal.append((k, p)),
                       on_closed=lambda s, r, f: closed.append((s, r, f)),
                       market_open=lambda: market)
    return SimpleNamespace(m=m, b=fake, a=alerts, c=clock, prot=prot, journal=journal, closed=closed)


def keys(alerts):
    return [k for k, *_ in alerts.raised]


def sells(b):
    return [c for c in b.calls if c[0] == "submit"]


# 1 ---------------------------------------------------------------------------------------
def test_normal_exit_reprices_tp_and_stop_stays_held_until_fill():
    t = make()
    assert t.m.request("AAPL", "sentiment -6") == em.REPRICING
    rep = t.b.calls[0]
    assert rep[0] == "replace" and rep[1] == t.b.tp["id"] and rep[2] == 99.9 and rep[3] == "sR-AAPL-abcdef123456-1"
    assert t.b.tp["status"] == "replaced" and len(t.b.working_stop()) == 1     # stop still held
    t.c.t += 2
    t.m.step("AAPL")
    assert t.m.active()["AAPL"] == em.EXIT_WORKING
    t.b.fill_live_tp(99.9)
    t.c.t += 2
    t.m.step("AAPL")
    assert "AAPL" not in t.m.exits and t.b.qty == 0
    assert len(t.b.sells_filled) == 1 and sells(t.b) == [] and t.b.working_stop() == []
    sym, reason, fill = t.closed[0]
    assert fill["filled_avg_price"] == 99.9 and reason == "sentiment -6"
    assert [p["to"] for k, p in t.journal if k == "exit_transition"][-2:] == [em.CLOSED, em.RECONCILED]


# 2 ---------------------------------------------------------------------------------------
def test_original_tp_fills_during_replace_no_second_sell_no_short():
    t = make()
    t.b.replace_behaviour = ["fill_first"]
    t.m.request("AAPL", "trailing")
    t.c.t += 1
    t.m.step("AAPL")
    assert t.b.qty == 0 and len(t.b.sells_filled) == 1 and sells(t.b) == []
    assert "AAPL" not in t.m.exits and t.closed


# 3 ---------------------------------------------------------------------------------------
def test_replace_rejected_twice_goes_to_fallback_and_a_stop_is_always_working():
    t = make()
    t.b.replace_behaviour = ["reject", "reject"]
    t.m.request("AAPL", "time")
    assert len(t.b.working_stop()) == 1
    t.c.t += 1
    t.m.step("AAPL")                                   # second, fresh price
    assert [c[0] for c in t.b.calls] == ["replace", "replace", "cancel", "cancel"]
    assert t.b.calls[0][2] == t.b.calls[1][2] == 99.9 and t.b.calls[0][3] == t.b.calls[1][3]   # rejected: nothing created at Alpaca, number reused
    assert t.m.active()["AAPL"] == em.FALLBACK_CANCEL
    t.c.t += 1
    t.m.step("AAPL")                                   # cancels confirmed -> sell
    assert sells(t.b) == [("submit", "sX-AAPL-abcdef123456-1", "10")]
    t.b.fill_x()
    t.c.t += 1
    t.m.step("AAPL")
    assert t.b.qty == 0 and "AAPL" not in t.m.exits


# 4 ---------------------------------------------------------------------------------------
def test_partial_fill_never_replaced_then_fallback_after_5_min():
    t = make()
    t.m.request("AAPL", "sentiment")
    t.b.fill_live_tp(99.9, qty=3)
    t.c.t += 30
    t.m.step("AAPL")
    replaces = [c for c in t.b.calls if c[0] == "replace"]
    t.c.t += 200
    t.m.step("AAPL")
    assert [c for c in t.b.calls if c[0] == "replace"] == replaces         # never replaced again
    t.c.t += 120
    t.m.step("AAPL")
    assert t.m.active()["AAPL"] == em.FALLBACK_CANCEL
    t.c.t += 1
    t.m.step("AAPL")
    assert sells(t.b) == [("submit", "sX-AAPL-abcdef123456-1", "7")]       # the remainder only


# 5 ---------------------------------------------------------------------------------------
def test_stop_fills_during_exit_closes_without_any_sell():
    t = make()
    t.m.request("AAPL", "sentiment")
    t.b._fill(t.b.stop, 90.0)                           # stop triggers first
    t.c.t += 1
    t.m.step("AAPL")
    assert t.b.qty == 0 and sells(t.b) == [] and "AAPL" not in t.m.exits
    assert t.closed[0][2]["filled_avg_price"] == 90.0


# 6 ---------------------------------------------------------------------------------------
def test_uncertain_replace_found_is_adopted_without_a_second_request():
    t = make()
    t.b.replace_behaviour = ["unknown"]                  # replace landed, response lost
    t.m.request("AAPL", "x")
    assert t.m.active()["AAPL"] == em.UNCERTAIN
    t.c.t += 5
    t.m.step("AAPL")
    assert t.m.active()["AAPL"] == em.EXIT_WORKING
    assert len([c for c in t.b.calls if c[0] == "replace"]) == 1


def test_uncertain_sell_not_found_retries_with_the_same_id_only():
    t = make()
    t.b.replace_behaviour = ["reject", "reject"]
    t.b.submit_behaviour = ["unknown_missing"]
    t.m.request("AAPL", "x")
    t.m.step("AAPL")
    t.c.t += 1
    t.m.step("AAPL")                                     # sell -> unknown, not at broker
    assert t.m.active()["AAPL"] == em.UNCERTAIN
    t.c.t += 5
    t.m.step("AAPL")                                     # confirmed not found
    t.c.t += 1
    t.m.step("AAPL")
    ids = [c[1] for c in sells(t.b)]
    assert ids == ["sX-AAPL-abcdef123456-1", "sX-AAPL-abcdef123456-1"]


# 7 ---------------------------------------------------------------------------------------
def test_follows_the_live_order_never_a_dead_id():
    t = make()
    t.m.request("AAPL", "x")
    for _ in range(2):                                   # 2 more prices after 120 s each
        t.c.t += 121
        t.m.step("AAPL")
    reps = [c for c in t.b.calls if c[0] == "replace"]
    assert len(reps) == 3 and len({c[1] for c in reps}) == 3          # three different live IDs
    assert [c[3][-1] for c in reps] == ["1", "2", "3"]                # fresh client ID each time
    for c in reps:
        assert t.b.by_id(c[1])["status"] == "replaced"


# 8 ---------------------------------------------------------------------------------------
def test_qty_mismatch_goes_to_fallback_not_a_patch():
    t = make()
    t.b.qty = 7.0                                        # position smaller than the legs
    t.m.request("AAPL", "x")
    assert not [c for c in t.b.calls if c[0] == "replace"]
    assert t.m.active()["AAPL"] == em.FALLBACK_CANCEL


# 9 ---------------------------------------------------------------------------------------
def test_unfilled_after_three_prices_escalates_and_stays_protected():
    t = make()
    t.m.request("AAPL", "x")
    for _ in range(3):
        t.c.t += 121
        t.m.step("AAPL")
    assert t.m.active()["AAPL"] == em.ESCALATED
    assert "exit_escalated:AAPL" in keys(t.a) and len(t.b.working_stop()) == 1
    assert not [c for c in t.b.calls if c[0] == "cancel"]


# 10 --------------------------------------------------------------------------------------
def test_cancel_not_confirmed_never_sells():
    t = make()
    t.b.replace_behaviour = ["reject", "reject"]
    t.b.cancel_behaviour = ["stuck", "stuck"]
    t.m.request("AAPL", "x")
    t.m.step("AAPL")
    for _ in range(3):
        t.c.t += 15
        t.m.step("AAPL")
    assert sells(t.b) == [] and "exit_uncertain:AAPL" in keys(t.a)


# 11 --------------------------------------------------------------------------------------
def test_failed_fallback_sell_restores_protection_in_the_same_step():
    t = make()
    t.b.replace_behaviour = ["reject", "reject"]
    t.b.submit_behaviour = ["reject"]
    t.m.request("AAPL", "x")
    t.m.step("AAPL")
    t.c.t += 1
    t.m.step("AAPL")
    assert t.prot.sweeps == 1 and t.m.active()["AAPL"] == em.EXIT_REQUESTED


def test_unfilled_fallback_sell_is_cancelled_then_protection_restored():
    t = make()
    t.b.replace_behaviour = ["reject", "reject"]
    t.m.request("AAPL", "x")
    t.m.step("AAPL")
    t.c.t += 1
    t.m.step("AAPL")                                     # X sent, never fills
    t.c.t += 61
    t.m.step("AAPL")
    assert ("cancel", next(o["id"] for o in t.b.orders if o["client_order_id"].startswith("sX-"))) in t.b.calls
    t.c.t += 1
    t.m.step("AAPL")
    assert t.prot.sweeps == 1


# 12 --------------------------------------------------------------------------------------
def test_foreign_order_escalates_hands_off():
    t = make()
    t.b._add(side="sell", type="limit", status="new", qty=1.0, cid="manual-from-dashboard", limit=150)
    t.m.request("AAPL", "x")
    assert t.b.calls == [] and "exit_escalated:AAPL" in keys(t.a)


# 13 --------------------------------------------------------------------------------------
@pytest.mark.parametrize("lease,market", [(False, True), (True, False)])
def test_no_lease_or_market_closed_sends_nothing(lease, market):
    t = make(lease=lease, market=market)
    t.m.request("AAPL", "x")
    t.c.t += 300
    t.m.step("AAPL")
    assert t.b.calls == [] and t.m.active()["AAPL"] == em.EXIT_REQUESTED


def test_unknown_broker_state_defers_and_alerts_after_two():
    t = make()
    t.b.read_ok = False
    t.m.request("AAPL", "x")
    t.m.step("AAPL")
    assert t.b.calls == [] and "exit_deferred:AAPL" in keys(t.a)


def test_request_is_idempotent():
    t = make()
    t.m.request("AAPL", "a")
    t.m.request("AAPL", "b")                               # same cycle, again
    assert len([c for c in t.b.calls if c[0] == "replace"]) == 1


def test_position_with_no_orders_is_protected_before_any_exit():
    t = make()
    for o in t.b.orders:
        if o["side"] == "sell":
            o["status"] = "canceled"
    t.b.held_for_orders = 0
    t.m.request("AAPL", "x")
    assert t.prot.sweeps == 1 and sells(t.b) == []


def test_protection_held_off_only_during_the_sell_window_and_at_most_5_min():
    t = make()
    t.b.replace_behaviour = ["reject", "reject"]
    t.m.request("AAPL", "x")
    pos = lambda: t.b.positions_snapshot().data.get("AAPL")          # noqa: E731
    hist = lambda: t.b.symbol_orders_snapshot("AAPL").data           # noqa: E731
    assert not t.m.holds_protection("AAPL", pos(), hist())   # REPRICING: stop still held
    t.m.step("AAPL")                                          # -> FALLBACK_CANCEL
    assert t.m.holds_protection("AAPL", pos(), hist())
    assert not t.m.holds_protection("AAPL")                   # no broker data: never hold off
    t.c.t += 81
    assert not t.m.holds_protection("AAPL", pos(), hist())    # cancel window over


def test_hold_off_follows_quantities_not_time():
    """Review 11 Nov: a working exit sell covering the whole position holds
    protection off; one covering FEWER shares than held does not."""
    t = make()
    t.b.replace_behaviour = ["reject", "reject"]
    t.m.request("AAPL", "x")
    t.m.step("AAPL")
    t.c.t += 1
    t.m.step("AAPL")                                          # X market sell for 10 working
    assert t.m.active()["AAPL"] == em.SELLING
    pos = t.b.positions_snapshot().data["AAPL"]
    assert t.m.holds_protection("AAPL", pos, t.b.symbol_orders_snapshot("AAPL").data)
    x = next(o for o in t.b.orders if o["client_order_id"].startswith("sX-"))
    x["qty"] = 6.0                                            # exit only covers 6 of 10
    assert not t.m.holds_protection("AAPL", pos, t.b.symbol_orders_snapshot("AAPL").data)


@pytest.mark.parametrize("quote,why", [
    ({"bid": 100.0, "ask": 100.1, "age_s": 60.0}, "stale"),
    ({"bid": 0.0, "ask": 0.0, "age_s": 1.0}, "halted"),
    ({"bid": 100.0, "ask": 103.0, "age_s": 1.0}, "illiquid"),
    (None, "no quote"),
])
def test_bad_quotes_are_not_priced_and_escalate_after_5_min(quote, why):
    t = make()
    t.b.quote = quote if quote is not None else {}
    if quote is None:
        t.b.latest_quote = lambda s: None
    t.m.request("AAPL", "x")
    assert not [c for c in t.b.calls if c[0] == "replace"]       # nothing priced
    t.c.t += 301
    t.m.step("AAPL")
    assert t.m.active()["AAPL"] == em.ESCALATED and len(t.b.working_stop()) == 1
    assert not [c for c in t.b.calls if c[0] in ("replace", "cancel")]


def test_does_not_chase_a_falling_price():
    t = make()
    t.m.request("AAPL", "x")                                      # first price at bid 100
    t.b.bid = 96.5                                                # -3.5% within the window
    t.c.t += 121
    t.m.step("AAPL")
    assert t.m.active()["AAPL"] == em.ESCALATED
    assert len([c for c in t.b.calls if c[0] == "replace"]) == 1   # no lower reprice
    assert len(t.b.working_stop()) == 1
