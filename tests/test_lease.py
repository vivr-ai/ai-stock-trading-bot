"""Reliability v3 2I: single-instance trading lease.

Scenario tests use an in-memory store with the same semantics as the SQL
(acquire only if expired or mine; renew only with my holder+epoch), a shared
"database clock" and per-instance monotonic clocks so stalls and partitions
can be simulated deterministically. tests/test_lease_pg.py runs the real SQL.
"""
import threading
from types import SimpleNamespace

import pytest

from bot.reliability.lease import (HELD, LOST, RECONCILING, STANDBY, InstanceLease,
                                   LeaseStoreError)
from bot.trading.alpaca_client import AlpacaBroker, OrderBlockedError


class DbClock:
    def __init__(self):
        self.t = 1000.0


class FakeStore:
    def __init__(self, dbclock):
        self.db = dbclock
        self.rows = {}
        self.events = []
        self.lock = threading.Lock()

    def acquire(self, name, holder, ttl):
        with self.lock:
            row = self.rows.get(name)
            if row is None:
                self.rows[name] = dict(holder=holder, epoch=1, exp=self.db.t + ttl)
                return 1
            if row["exp"] < self.db.t or row["holder"] == holder:
                row.update(holder=holder, epoch=row["epoch"] + 1, exp=self.db.t + ttl)
                return row["epoch"]
            return None

    def renew(self, name, holder, epoch, ttl):
        with self.lock:
            row = self.rows.get(name)
            if row and row["holder"] == holder and row["epoch"] == epoch:
                row["exp"] = self.db.t + ttl
                return True
            return False

    def release(self, name, holder, epoch):
        with self.lock:
            row = self.rows.get(name)
            if row and row["holder"] == holder and row["epoch"] == epoch:
                row["exp"] = self.db.t - 0.001   # Postgres: a later statement's now() is later
                return True
            return False

    def log_event(self, *a):
        self.events.append(a)


class Partitionable:
    """One instance's view of the store; `down=True` = it can't reach the DB."""

    def __init__(self, store):
        self.store, self.down = store, False

    def __getattr__(self, name):
        target = getattr(self.store, name)

        def call(*a, **k):
            if self.down and name != "log_event":
                raise LeaseStoreError("could not connect to server")
            return target(*a, **k)
        return call


class Mono:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Alerts:
    def __init__(self):
        self.raised, self.resolved = [], []

    def raise_(self, key, text, severity="warning", repeat_s=None):
        self.raised.append((key, severity))
        return True

    def resolve(self, key, text=None, severity="warning"):
        self.resolved.append(key)
        return True


def make(store, name="A", reconcile=None):
    view, mono, alerts = Partitionable(store), Mono(), Alerts()
    lease = InstanceLease(view, "trader:****N70Z", holder=name, alerts=alerts,
                          reconcile=reconcile or (lambda: (True, "2 positions, 2 protected")),
                          monotonic=mono)
    return lease, view, mono, alerts


def advance(db, monos, seconds):
    db.t += seconds
    for m in monos:
        m.t += seconds


def test_concurrent_startup_exactly_one_holder():
    db = DbClock()
    store = FakeStore(db)
    leases = [make(store, f"I{i}")[0] for i in range(8)]
    results = []
    threads = [threading.Thread(target=lambda l=l: results.append(l.ensure_ready()[0])) for l in leases]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1
    assert sum(1 for l in leases if l.state == HELD) == 1
    for l in leases:
        if l.state != HELD:
            assert l.can_submit("exit")[0] is False


def test_takeover_requires_reconciliation_before_any_order():
    db = DbClock()
    store = FakeStore(db)
    calls = []
    a, *_ = make(store, "A", reconcile=lambda: (calls.append(1) or False, "positions unreadable"))
    assert a.try_acquire() and a.state == RECONCILING
    assert a.can_submit("exit")[0] is False            # not before reconciliation
    ok, why = a.ensure_ready()
    assert not ok and "reconciliation failed" in why and calls
    a.reconcile = lambda: (True, "ok")
    assert a.ensure_ready()[0] and a.state == HELD


def test_overlapping_deployment_and_clean_release():
    db = DbClock()
    store = FakeStore(db)
    a, _, ma, _ = make(store, "old")
    b, _, mb, alerts_b = make(store, "new")
    assert a.ensure_ready()[0]
    assert not b.ensure_ready()[0] and b.state == STANDBY      # overlap: new one waits
    a.release()                                                # SIGTERM on the old one
    assert b.ensure_ready()[0] and b.state == HELD             # takes over at once
    assert store.rows["trader:****N70Z"]["epoch"] == 2


def test_crash_lease_expires_after_ttl_then_takeover():
    db = DbClock()
    store = FakeStore(db)
    a, _, ma, _ = make(store, "A")
    b, _, mb, _ = make(store, "B")
    assert a.ensure_ready()[0]
    advance(db, [mb], 89)                                       # A crashed: no renewals
    assert not b.ensure_ready()[0]
    advance(db, [mb], 2)                                        # 91 s: expired
    assert b.ensure_ready()[0]


def test_renewals_keep_lease_and_freshness():
    db = DbClock()
    store = FakeStore(db)
    a, _, ma, _ = make(store, "A")
    b, _, mb, _ = make(store, "B")
    assert a.ensure_ready()[0]
    for _ in range(20):                                         # 400 s of normal running
        advance(db, [ma, mb], 20)
        a.tick()
        b.tick()
    assert a.can_submit("exit")[0] and b.state == STANDBY


def test_stalled_process_stops_then_loses_on_wake():
    db = DbClock()
    store = FakeStore(db)
    a, _, ma, alerts_a = make(store, "A")
    b, _, mb, _ = make(store, "B")
    assert a.ensure_ready()[0]
    advance(db, [ma, mb], 61)                                   # A frozen, no renewals
    assert a.can_submit("exit")[0] is False                     # stale: nothing goes out
    advance(db, [ma, mb], 40)                                   # 101 s: expired
    assert b.ensure_ready()[0]
    a.tick()                                                    # A wakes up and renews
    assert a.state == LOST and a.can_submit("exit")[0] is False
    assert ("lease_lost", "critical") in alerts_a.raised


def test_network_partition_d8_policy():
    db = DbClock()
    store = FakeStore(db)
    a, va, ma, alerts_a = make(store, "A")
    b, _, mb, _ = make(store, "B")
    assert a.ensure_ready()[0]
    va.down = True                                              # only A loses the DB
    advance(db, [ma, mb], 20); a.tick()
    assert a.can_submit("exit")[0]                              # within 60 s: exits/protection ok
    advance(db, [ma, mb], 40); a.tick()
    assert a.can_submit("exit")[0] is False                     # 60 s: everything paused
    assert ("lease_unverified", "critical") in alerts_a.raised
    advance(db, [ma, mb], 31)                                   # 91 s: B may take over
    assert b.ensure_ready()[0]
    va.down = False
    a.tick()                                                    # partition heals
    assert a.state == LOST


def test_db_down_for_everyone_then_back_requires_rereconcile():
    db = DbClock()
    store = FakeStore(db)
    recon = []
    a, va, ma, _ = make(store, "A", reconcile=lambda: (recon.append(1) or True, "ok"))
    assert a.ensure_ready()[0] and len(recon) == 1
    va.down = True
    advance(db, [ma], 300)                                      # 5-minute outage
    a.tick()
    assert not a.ensure_ready()[0]                              # no orders during the outage
    va.down = False                                             # nobody else could acquire
    ok, _ = a.ensure_ready()
    assert ok and a.state == HELD and len(recon) == 2           # re-reconciled after the gap


def test_lost_instance_can_reacquire_later_with_new_epoch():
    db = DbClock()
    store = FakeStore(db)
    a, _, ma, _ = make(store, "A")
    b, _, mb, _ = make(store, "B")
    a.ensure_ready()
    advance(db, [ma, mb], 95)
    b.ensure_ready()
    a.tick()
    assert a.state == LOST
    b.release()
    assert a.ensure_ready()[0] and a.epoch == 3


def test_standby_alert_after_ten_minutes():
    db = DbClock()
    store = FakeStore(db)
    a, _, ma, _ = make(store, "A")
    b, _, mb, alerts_b = make(store, "B")
    a.ensure_ready()
    for _ in range(31):
        advance(db, [ma, mb], 20)
        a.tick()
        b.tick()
    assert ("lease_standby", "critical") in alerts_b.raised


def _broker_with_guard(guard):
    br = AlpacaBroker.__new__(AlpacaBroker)
    br.allow_submit, br.mode, br.order_guard = True, "paper", guard
    br._trading = None
    return br


def test_broker_refuses_orders_without_lease():
    db = DbClock()
    store = FakeStore(db)
    a, *_ = make(store, "A")
    br = _broker_with_guard(a.can_submit)                       # A never acquired
    with pytest.raises(OrderBlockedError):
        br.submit_bracket_buy("AAPL", 1, 90.0, 150.0, client_order_id="AAPL-x")
    with pytest.raises(OrderBlockedError):
        br.submit_protective_exit("AAPL", 1, 90.0, 150.0, client_order_id="sP-AAPL-x-1")
    assert br.close_position("AAPL") is None                    # nothing cancelled or sold
    br.cancel_orders_for("AAPL")                                # silently refused, no API call


def test_no_lease_configured_means_no_guard():
    br = _broker_with_guard(None)
    br._guard("entry", "x")                                     # no exception


def test_run_cycle_on_standby_touches_nothing():
    from bot.trading.strategy import SentimentStrategy

    class NoBroker:
        def __getattr__(self, name):
            raise AssertionError(f"broker.{name} called while on standby")

    s = SentimentStrategy.__new__(SentimentStrategy)
    s.broker = NoBroker()
    s.lease = type("L", (), {"ensure_ready": lambda self: (False, "standby: another instance")})()
    s.run_cycle()
    assert s.last_cycle_outcome == "standby"


def test_renewal_failure_logged_at_most_once_a_minute(caplog):
    """DB outage drill 12 Nov: ~4 warnings a minute for 45 minutes."""
    from bot.reliability.lease import LeaseStoreError

    class Down:
        def renew(self, *a):
            raise LeaseStoreError("db down")

    t = {"m": 0.0}
    lease = InstanceLease.__new__(InstanceLease)
    from bot.reliability import lease as L
    import threading
    lease.store, lease.name, lease.holder, lease.epoch, lease.ttl_s = Down(), "n", "h", 1, 90
    lease.state, lease.max_age_s, lease.last_ok = L.HELD, 60, 0.0
    lease.mono = lambda: t["m"]
    lease.alerts = SimpleNamespace(raise_=lambda *a, **k: None, resolve=lambda *a, **k: None)
    lease._lock = threading.RLock()
    with caplog.at_level("WARNING"):
        for i in range(12):                     # 12 renew attempts over 2 minutes
            t["m"] = 10.0 * i
            lease.renew()
    assert sum("Lease renewal failed" in r.message for r in caplog.records) == 2
