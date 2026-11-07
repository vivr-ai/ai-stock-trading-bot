"""Reliability v3 2I: the lease SQL against a real (throwaway) Postgres."""
import threading
import time

import psycopg2

from bot.reliability.lease import InstanceLease, LeaseStoreError, PgLeaseStore
from tests.pg_fixture import pg_url  # noqa: F401 - fixture


def _reset(url, name):
    with psycopg2.connect(url) as c, c.cursor() as cur:
        cur.execute("DELETE FROM bot_lease WHERE name = %s", (name,))


def test_atomic_acquire_under_contention(pg_url):
    name = "trader:contention"
    _reset(pg_url, name)
    store = PgLeaseStore(pg_url)
    wins, barrier = [], threading.Barrier(12)

    def go(i):
        barrier.wait()
        if store.acquire(name, f"inst-{i}", 90) is not None:
            wins.append(i)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(wins) == 1
    assert store.current(name)["holder"] == f"inst-{wins[0]}"


def test_renew_release_takeover_and_epoch(pg_url):
    name = "trader:cycle"
    _reset(pg_url, name)
    s = PgLeaseStore(pg_url)
    e1 = s.acquire(name, "A", 90)
    assert e1 == 1 and s.acquire(name, "B", 90) is None
    assert s.renew(name, "A", e1, 90)
    assert s.release(name, "A", e1)
    time.sleep(0.01)
    e2 = s.acquire(name, "B", 90)
    assert e2 == 2
    assert s.renew(name, "A", e1, 90) is False          # old holder is fenced out
    assert s.renew(name, "B", e2, 90)


def test_expiry_uses_database_clock(pg_url):
    name = "trader:expiry"
    _reset(pg_url, name)
    s = PgLeaseStore(pg_url)
    assert s.acquire(name, "A", 0.5) == 1
    assert s.acquire(name, "B", 90) is None
    time.sleep(0.7)
    assert s.acquire(name, "B", 90) == 2


def test_unreachable_database_raises_store_error():
    s = PgLeaseStore("postgresql://postgres@127.0.0.1:1/none")
    try:
        s.acquire("x", "A", 90)
        raise AssertionError("expected LeaseStoreError")
    except LeaseStoreError:
        pass


def test_instance_lease_end_to_end_with_events(pg_url):
    name = "trader:e2e"
    _reset(pg_url, name)
    a = InstanceLease(PgLeaseStore(pg_url), name, holder="A")
    b = InstanceLease(PgLeaseStore(pg_url), name, holder="B")
    assert a.ensure_ready()[0]
    assert not b.ensure_ready()[0]
    a.release()
    assert b.ensure_ready()[0]
    with psycopg2.connect(pg_url) as c, c.cursor() as cur:
        cur.execute("SELECT holder, event FROM bot_lease_events WHERE name = %s ORDER BY id", (name,))
        events = cur.fetchall()
    assert ("A", "acquired") in events and ("A", "released") in events and ("B", "reconciled") in events
