"""Unit tests for Recorder.prune_old_decisions (bot/persistence/db.py) - the
daily decisions-table retention job. psycopg2 isn't installed in this test
environment (and shouldn't need to be, for pure Python logic), so these
tests fake out Recorder._psycopg2 directly rather than hitting a real
database - the same trick used to test the rest of this module's SQL-free
logic elsewhere in the suite.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.persistence.db import Recorder


class _FakeCursor:
    def __init__(self, batch_rowcounts, calls):
        self._batch_rowcounts = list(batch_rowcounts)
        self._calls = calls
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self._calls.append({"sql": sql, "params": dict(params)})
        self.rowcount = self._batch_rowcounts.pop(0) if self._batch_rowcounts else 0


class _FakeConn:
    def __init__(self, batch_rowcounts):
        self.calls = []
        self._cursor = _FakeCursor(batch_rowcounts, self.calls)
        self.committed = 0
        self.rolled_back = 0
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


class _FakePsycopg2:
    def __init__(self, conn):
        self._conn = conn

    def connect(self, *args, **kwargs):
        return self._conn


def _make_recorder(conn) -> Recorder:
    # Bypasses __init__'s real `import psycopg2` (not installed in this test
    # env) - same as production, self.enabled gates every DB method, so
    # forcing it True + swapping in the fake module is enough to exercise
    # prune_old_decisions's actual batching/commit logic.
    recorder = Recorder(database_url="postgres://fake/db")
    recorder.enabled = True
    recorder._psycopg2 = _FakePsycopg2(conn)
    return recorder


def test_disabled_recorder_returns_none_and_touches_nothing():
    recorder = Recorder(database_url="")  # no DATABASE_URL -> disabled
    assert recorder.prune_old_decisions() is None


def test_single_batch_under_batch_size_deletes_and_stops():
    conn = _FakeConn(batch_rowcounts=[42])  # fewer than batch_size -> one pass
    recorder = _make_recorder(conn)

    deleted = recorder.prune_old_decisions(retention_days=120, batch_size=5000)

    assert deleted == 42
    assert len(conn.calls) == 1
    assert conn.committed == 1
    assert conn.closed is True
    params = conn.calls[0]["params"]
    assert params["retention_days"] == 120
    assert params["batch_size"] == 5000


def test_multiple_full_batches_loop_until_short_batch():
    # Two full batches (batch_size rows each) then a short one -> 3 calls,
    # 3 separate commits (never one long-running transaction), then stop.
    conn = _FakeConn(batch_rowcounts=[100, 100, 30])
    recorder = _make_recorder(conn)

    deleted = recorder.prune_old_decisions(retention_days=120, batch_size=100)

    assert deleted == 230
    assert len(conn.calls) == 3
    assert conn.committed == 3


def test_zero_rows_to_delete_makes_exactly_one_call():
    conn = _FakeConn(batch_rowcounts=[0])
    recorder = _make_recorder(conn)

    deleted = recorder.prune_old_decisions(retention_days=120, batch_size=5000)

    assert deleted == 0
    assert len(conn.calls) == 1


def test_query_excludes_shadow_decisions_from_deletion():
    """The critical exclusion: reversion_shadow_buy/reversion_shadow_exit
    rows must never be deleted at any age, since Path B's shadow-verdict-
    readiness sample (dashboard's Shadow vs Live page) depends on the
    all-time earliest one of those rows."""
    conn = _FakeConn(batch_rowcounts=[0])
    recorder = _make_recorder(conn)

    recorder.prune_old_decisions(retention_days=120, batch_size=5000)

    sql = conn.calls[0]["sql"]
    assert "reversion_shadow_buy" in sql
    assert "reversion_shadow_exit" in sql
    assert "NOT IN" in sql


def test_query_never_touches_trades_or_closed_trades_or_notifications():
    conn = _FakeConn(batch_rowcounts=[0])
    recorder = _make_recorder(conn)

    recorder.prune_old_decisions(retention_days=120, batch_size=5000)

    sql = conn.calls[0]["sql"]
    assert "FROM decisions" in sql
    for other_table in ("trades", "closed_trades", "notifications"):
        assert other_table not in sql


def test_connect_failure_returns_none():
    class _RaisingPsycopg2:
        def connect(self, *args, **kwargs):
            raise RuntimeError("connection refused")

    recorder = Recorder(database_url="postgres://fake/db")
    recorder.enabled = True
    recorder._psycopg2 = _RaisingPsycopg2()

    assert recorder.prune_old_decisions() is None


def test_error_mid_run_rolls_back_and_returns_rows_deleted_so_far():
    class _FailingCursor(_FakeCursor):
        def execute(self, sql, params):
            self._calls.append({"sql": sql, "params": dict(params)})
            if len(self._calls) == 2:
                raise RuntimeError("connection dropped")
            self.rowcount = self._batch_rowcounts.pop(0) if self._batch_rowcounts else 0

    class _FailingConn(_FakeConn):
        def __init__(self, batch_rowcounts):
            self.calls = []
            self._cursor = _FailingCursor(batch_rowcounts, self.calls)
            self.committed = 0
            self.rolled_back = 0
            self.closed = False

    conn = _FailingConn(batch_rowcounts=[100, 100, 30])
    recorder = _make_recorder(conn)

    deleted = recorder.prune_old_decisions(retention_days=120, batch_size=100)

    # First batch (100) committed before the second call raised.
    assert deleted == 100
    assert conn.committed == 1
    assert conn.rolled_back == 1
    assert conn.closed is True
    assert recorder.healthy is False
