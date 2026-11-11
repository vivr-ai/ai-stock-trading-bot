"""Stage 6c: the failure-injection scenarios (design tests 1-14, plus 15-17)
against the scripted fake Alpaca, with the soak invariants checked after
every step:
  * never short;  * never more sold than held;
  * at most one working non-stop sell per position (one exit at a time).
Tests 1-6, 8 and 9 are in test_exit_manager.py; this file adds 7, 10-17 and
the injector itself. The same injector runs on the staging paper account
(scripts/fault_drill.py) - those runs are the soak evidence, not these."""
from types import SimpleNamespace

import pytest

from bot.reliability import exit_manager as em
from bot.reliability.fault_injection import FaultInjector, from_env
from tests.pg_fixture import pg_url  # noqa: F401 - fixture
from tests.test_exit_manager import Alerts, Clock, FakeAlpaca, Prot, keys, sells


def invariants(b, start_qty=10.0):
    assert b.qty >= 0, "short position"
    assert sum(q for _, q, _ in b.sells_filled) <= start_qty, "sold more than held"
    live = [o for o in b.orders if o["side"] == "sell" and o["type"] != "stop"
            and o["status"] in ("new", "partially_filled", "pending_replace")]
    assert len(live) <= 1, f"two working sells: {[o['client_order_id'] for o in live]}"


def manager(b, clock, alerts=None, prot=None, lease=lambda: True, faults=None):
    m = em.ExitManager(b, alerts or Alerts(), "s", protection=prot or Prot(), lease_ok=lease,
                       clock=clock, market_open=lambda: True)
    if faults is not None:
        faults.wrap(b)
        m.faults = faults
    return m


def run(m, b, clock, steps=1, dt=1.0, sym="AAPL"):
    for _ in range(steps):
        clock.t += dt
        m.step(sym)
        invariants(b)


# 7 --------------------------------------------------------------------------------------
def test_7_stale_ids_after_two_reprices_and_a_restart():
    b, c = FakeAlpaca(), Clock()
    m = manager(b, c)
    m.request("AAPL", "x")
    run(m, b, c, steps=1, dt=121)                   # 2nd price: replaced_by chain of 2
    dead = [o["id"] for o in b.orders if o["status"] == "replaced"]
    assert len(dead) == 2
    m2 = manager(b, c)                              # restart: no memory
    m2.request("AAPL", "x")
    assert m2.active()["AAPL"] == em.EXIT_WORKING   # adopted the live replacement
    run(m2, b, c, steps=1, dt=121)
    sent_to = [call[1] for call in b.calls if call[0] == "replace"]
    assert not set(sent_to[2:]) & set(dead)          # nothing sent to a dead ID
    assert b.calls[-1][3] == "sR-AAPL-abcdef123456-3"


# 10 -------------------------------------------------------------------------------------
def test_10_halt_no_fills_never_cancels_protection_and_resumes():
    b, c, a = FakeAlpaca(), Clock(), Alerts()
    m = manager(b, c, alerts=a)
    m.request("AAPL", "x")
    run(m, b, c, steps=4, dt=121)                   # halted: nothing fills
    assert m.active()["AAPL"] == em.ESCALATED and "exit_escalated:AAPL" in keys(a)
    assert not [x for x in b.calls if x[0] == "cancel"] and len(b.working_stop()) == 1
    b.fill_live_tp(98.0)                            # halt lifts, the live limit fills
    run(m, b, c)
    assert b.qty == 0 and "AAPL" not in m.exits


# 11 -------------------------------------------------------------------------------------
def test_11_same_exit_decided_twice_and_again_after_restart():
    b, c = FakeAlpaca(), Clock()
    m = manager(b, c)
    m.request("AAPL", "sentiment -6")
    m.request("AAPL", "trailing stop")              # same cycle, second reason
    invariants(b)
    m2 = manager(b, c)
    m2.request("AAPL", "sentiment -6")              # repeat after a restart
    invariants(b)
    series = {x[3].rsplit("-", 1)[0] for x in b.calls if x[0] == "replace"}
    assert series == {"sR-AAPL-abcdef123456"} and len([x for x in b.calls if x[0] == "replace"]) == 1


# 12 -------------------------------------------------------------------------------------
KILL_POINTS = [em.REPRICING, em.EXIT_WORKING, em.FALLBACK_CANCEL, em.SELLING, em.UNCERTAIN,
               em.RESTORE_PROTECTION, em.ESCALATED, em.CLOSED, em.EXIT_REQUESTED, "partial"]


class Killed(Exception):
    pass


def _scenario_for(point, b):
    if point == em.FALLBACK_CANCEL or point == em.SELLING:
        b.replace_behaviour = ["reject", "reject"]
    if point == em.UNCERTAIN:
        b.replace_behaviour = ["unknown"]
    if point == em.RESTORE_PROTECTION:
        b.replace_behaviour = ["reject", "reject"]
        b.submit_behaviour = ["reject"]


@pytest.mark.parametrize("point", KILL_POINTS)
def test_12_restart_at_each_kill_point_rebuilds_from_alpaca(point):
    b, c = FakeAlpaca(), Clock()
    _scenario_for(point, b)
    spec = "" if point == "partial" else f"kill_at:{point}"
    faults = FaultInjector(spec, allow=True, kill=lambda code: (_ for _ in ()).throw(Killed()))
    m = manager(b, c, faults=faults)
    try:
        m.request("AAPL", "x")
        if point == "partial":
            b.fill_live_tp(99.9, qty=3)
            raise Killed()
        if point == em.CLOSED:
            b.fill_live_tp(99.9)
        for _ in range(8):
            c.t += 130
            m.step("AAPL")
    except Killed:
        pass
    invariants(b)
    m2 = manager(b, c)                               # new process, nothing remembered
    m2.request("AAPL", "x")
    for _ in range(12):
        c.t += 130
        if "AAPL" not in m2.exits:
            break
        if b.working_stop() == [] and b.qty > 0 and not [o for o in b.orders if o["status"] == "new"]:
            pass
        m2.step("AAPL")
        invariants(b)
        if m2.exits.get("AAPL") and m2.exits["AAPL"].state == em.ESCALATED:
            b.fill_live_tp(98.0)
    # Either closed, or still held with a stop working / protection asked for first.
    assert b.qty == 0 or b.working_stop() or m2.protection.sweeps >= 1


# 13 -------------------------------------------------------------------------------------
def test_13_two_instances_only_the_lease_holder_acts():
    b, c = FakeAlpaca(), Clock()
    holder = {"who": "A"}
    a = manager(b, c, lease=lambda: holder["who"] == "A")
    bb = manager(b, c, lease=lambda: holder["who"] == "B")
    a.request("AAPL", "x")
    bb.request("AAPL", "x")                          # B: no lease, sends nothing
    assert len(b.calls) == 1
    holder["who"] = "B"                              # A stalls, B takes over after reconciling
    c.t += 200
    a.step("AAPL")                                   # A is fenced out
    bb.step("AAPL")
    invariants(b)
    assert len([x for x in b.calls if x[0] == "replace"]) == 1   # B adopted A's live replacement
    assert bb.active()["AAPL"] == em.EXIT_WORKING


# 14 -------------------------------------------------------------------------------------
def test_14_failed_exit_then_failed_restores_escalate():
    b, c, al = FakeAlpaca(), Clock(), Alerts()
    b.replace_behaviour = ["reject"] * 10
    b.submit_behaviour = ["reject"] * 10
    m = manager(b, c, alerts=al)
    m.request("AAPL", "x")
    for _ in range(12):
        c.t += 30
        m.step("AAPL")
        invariants(b)
    assert m.protection.sweeps >= 3 and "exit_failed:AAPL" in keys(al)
    assert all(sev == "critical" for k, sev, _ in al.raised if k == "exit_failed:AAPL")


# 15 broker outage -----------------------------------------------------------------------
def test_15_broker_reads_fail_nothing_is_sent_then_recovers():
    b, c, al = FakeAlpaca(), Clock(), Alerts()
    m = manager(b, c, alerts=al)
    b.read_ok = False
    m.request("AAPL", "x")
    run(m, b, c, steps=3, dt=60)
    assert b.calls == [] and "exit_deferred:AAPL" in keys(al)
    b.read_ok = True
    run(m, b, c)
    assert [x[0] for x in b.calls] == ["replace"]


# 16 DB outage / lease unverifiable mid-exit (D8: pause everything) -----------------------
def test_16_lease_lost_mid_exit_pauses_all_submissions_then_reconciles():
    b, c = FakeAlpaca(), Clock()
    ok = {"v": True}
    m = manager(b, c, lease=lambda: ok["v"])
    m.request("AAPL", "x")
    ok["v"] = False
    run(m, b, c, steps=3, dt=130)
    assert len(b.calls) == 1                         # no reprice, cancel or sell while unverified
    ok["v"] = True
    run(m, b, c, dt=1)
    assert len([x for x in b.calls if x[0] == "replace"]) == 2


# 17 position already below its stop level ----------------------------------------------
def test_17_below_stop_exit_goes_through_the_state_machine():
    b, c = FakeAlpaca(stop_price=90.0), Clock()
    b.bid = 85.0                                     # gapped below the stop
    m = manager(b, c)
    m.request("AAPL", "emergency: price below stop level")
    rep = [x for x in b.calls if x[0] == "replace"][0]
    assert rep[2] == 84.91 and len(b.working_stop()) == 1     # marketable, stop still held
    b.fill_live_tp(84.9)
    run(m, b, c)
    assert b.qty == 0 and sells(b) == []


# injector ------------------------------------------------------------------------------
def test_injector_refuses_outside_staging():
    with pytest.raises(RuntimeError, match="only allowed"):
        FaultInjector("sell_reject", env={"BOT_ENVIRONMENT": "production"})
    with pytest.raises(RuntimeError):
        from_env({"STAGING_FAULTS": "sell_reject"})            # no BOT_ENVIRONMENT at all
    assert from_env({"BOT_ENVIRONMENT": "production"}) is None  # unset: nothing loads
    assert from_env({"BOT_ENVIRONMENT": "staging", "STAGING_FAULTS": "sell_reject:2"}).counts == {
        "sell_reject": 2}
    with pytest.raises(ValueError, match="unknown fault"):
        FaultInjector("drop_everything", allow=True)


def test_injected_replace_drop_response_is_adopted_not_resent():
    b, c = FakeAlpaca(), Clock()
    m = manager(b, c, faults=FaultInjector("replace_drop_response", allow=True))
    m.request("AAPL", "x")
    assert m.active()["AAPL"] == em.UNCERTAIN
    run(m, b, c, dt=5)
    assert m.active()["AAPL"] == em.EXIT_WORKING and len([x for x in b.calls if x[0] == "replace"]) == 1


def test_injected_sell_lost_retries_with_the_same_id():
    b, c = FakeAlpaca(), Clock()
    b.replace_behaviour = ["reject", "reject"]
    m = manager(b, c, faults=FaultInjector("sell_lost", allow=True))
    m.request("AAPL", "x")
    run(m, b, c, steps=4, dt=2)
    ids = [x[1] for x in b.calls if x[0] == "submit"]
    assert ids == ["sX-AAPL-abcdef123456-1"] and m.faults.fired[0][0] == "sell_lost"


# DB outage drill via injection ----------------------------------------------------------
def test_db_outage_window_refuses_connections_only_inside_the_window():
    import psycopg2

    f = FaultInjector("db_outage:1-3", allow=True)
    t = {"now": 0.0}
    rec = SimpleNamespace(_psycopg2=psycopg2)
    store = SimpleNamespace(_pg=psycopg2)
    proxy = f.wrap_db(recorder=rec, lease_store=store, clock=lambda: t["now"])
    assert rec._psycopg2 is proxy and store._pg is proxy
    assert not f.db_down(30) and f.db_down(61) and f.db_down(179) and not f.db_down(181)
    t["now"] = 90
    with pytest.raises(psycopg2.OperationalError, match="injected DB outage"):
        proxy.connect("postgresql://x")
    assert proxy.OperationalError is psycopg2.OperationalError     # the rest passes through
    assert [n for n, _ in f.fired] == ["db_outage"]
    with pytest.raises(ValueError):
        FaultInjector("db_outage:5-2", allow=True)


def test_db_outage_drill_end_to_end_with_real_postgres(pg_url, tmp_path):
    """The recorder and the lease both lose the DB inside the window and get
    it back after - against a real throwaway Postgres."""
    from bot.persistence.db import Recorder
    from bot.reliability.lease import InstanceLease, PgLeaseStore

    f = FaultInjector("db_outage:1-2", allow=True)
    t = {"now": 0.0}
    rec = Recorder(database_url=pg_url)
    store = PgLeaseStore(pg_url)
    f.wrap_db(recorder=rec, lease_store=store, clock=lambda: t["now"])
    assert rec.health_check()
    t["now"] = 70
    assert not rec.health_check()
    from bot.reliability.lease import LeaseStoreError
    with pytest.raises(LeaseStoreError):
        store.acquire("trader:drill", "A", 90)
    t["now"] = 130
    assert rec.health_check() and store.acquire("trader:drill", "A", 90) is not None


# Review 11 Nov 2B: protection manager x exit manager on ONE fake broker ---------------
class SharedBroker(FakeAlpaca):
    """FakeAlpaca plus the reads/writes the protection manager uses."""

    def latest_price(self, sym):
        return self.bid

    def last_fill(self, sym, side="buy"):
        return SimpleNamespace(order_id="abcdef1234567890")

    def open_orders_snapshot(self):
        from bot.trading.alpaca_client import BrokerSnapshot
        return BrokerSnapshot(True, [dict(o) for o in self.orders if o["status"] in
                                     ("new", "held", "partially_filled", "accepted", "pending_new")], "")

    def submit_oco_once(self, symbol, qty, stop_price, take_profit_price, client_order_id):
        self.calls.append(("oco", client_order_id, qty))
        avail = self.positions_snapshot().data["AAPL"]["qty_available"]
        if avail + 1e-6 < qty:
            from bot.trading.alpaca_client import OrderRejected
            raise OrderRejected("insufficient qty available", 403)
        g = f"oco{self.n}"
        tp = self._add(side="sell", type="limit", status="new", qty=qty, role="take_profit", group=g,
                       limit=take_profit_price, cid=client_order_id, oclass="oco")
        self._add(side="sell", type="stop", status="held", qty=qty, role="stop_loss", group=g,
                  stop=stop_price, oclass="oco")
        self.held_for_orders = qty
        return dict(tp)


def _pair():
    from bot.reliability.protection import ProtectionManager
    from tests.test_protection import RISK

    b, c, al = SharedBroker(), Clock(), Alerts()
    pm = ProtectionManager(b, RISK, "s", al, clock=c)
    xm = em.ExitManager(b, al, "s", protection=pm, clock=c, market_open=lambda: True)
    pm.hold_off = xm.holds_protection
    return b, c, al, pm, xm


def test_no_conflicting_stop_while_the_exit_sell_is_working():
    b, c, al, pm, xm = _pair()
    b.replace_behaviour = ["reject", "reject"]
    xm.request("AAPL", "x")
    xm.step("AAPL")                         # legs cancelled
    pm.sweep()                              # FALLBACK_CANCEL window: held off
    c.t += 1
    xm.step("AAPL")                         # X sell for 10 sent
    for _ in range(3):
        c.t += 10
        pm.sweep()                          # X covers the whole position: held off
        invariants(b)
    assert not [x for x in b.calls if x[0] == "oco"]


def test_remaining_shares_are_protected_when_the_exit_covers_fewer_than_held():
    b, c, al, pm, xm = _pair()
    b.replace_behaviour = ["reject", "reject"]
    xm.request("AAPL", "x")
    xm.step("AAPL")
    c.t += 1
    xm.step("AAPL")
    x = next(o for o in b.orders if o["client_order_id"].startswith("sX-"))
    b._fill(x, 99.0, qty=4)                 # 4 sold, X partially filled
    x["status"] = "canceled"                # ...and the rest of the X is cancelled
    b.held_for_orders = 0.0
    c.t += 61
    pm.sweep()
    ocos = [x for x in b.calls if x[0] == "oco"]
    assert ocos and ocos[0][2] == 6.0        # the confirmed remainder, not the original 10
    invariants(b)


def test_stuck_exit_never_leaves_a_position_unprotected_beyond_the_cap():
    b, c, al, pm, xm = _pair()
    b.replace_behaviour = ["reject", "reject"]
    b.cancel_behaviour = ["ok", "ok"]
    xm.request("AAPL", "x")
    xm.step("AAPL")                         # legs cancelled; exit then stalls (no more steps)
    c.t += 90                               # past the cancel window
    pm.sweep()
    assert [x for x in b.calls if x[0] == "oco"]
