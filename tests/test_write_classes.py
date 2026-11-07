"""Reliability v3 2G: critical vs non-critical DB writes."""
import bot.persistence.db as dbmod
from bot.persistence.db import Recorder
from bot.reliability.db_gate import DOWN, HEALTHY, DbHealthGate
from bot.reliability.journal import Journal


class FlakyPg:
    """Fails the first `fail_n` connects, then succeeds. health_check works
    whenever `health_ok` is True."""

    def __init__(self, fail_n=0, forever=False):
        self.fail_n, self.forever, self.connects = fail_n, forever, 0

    def connect(self, *a, **k):
        self.connects += 1
        if self.forever or self.connects <= self.fail_n:
            raise Exception("server closed the connection unexpectedly")
        return _Conn()


class _Cur:
    description = None

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchone(self):
        return tuple(dbmod.REQUIRED_TABLES)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def cursor(self):
        return _Cur()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def rec_with(pg, tmp_path):
    r = Recorder(database_url="postgres://x")
    r.enabled, r._psycopg2 = True, pg
    r.journal = Journal(str(tmp_path))
    return r


def gate_for(r, alerts):
    g = DbHealthGate(r, alert=lambda t, s: alerts.append((t, s)), reconcile=lambda: (True, "ok"))
    return g


def test_transient_failure_is_retried_without_journal_or_flags(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(dbmod, "_sleep", sleeps.append)
    r = rec_with(FlakyPg(fail_n=2), tmp_path)
    r.record_decision(symbol="AAPL", decision="scan")
    assert sleeps == [0.5, 1.0]
    assert r.journal.pending() == [] and not r.critical_failed and r.healthy


def test_noncritical_failure_journals_alerts_and_does_not_block_entries(tmp_path, monkeypatch):
    sleeps, alerts, warned = [], [], []
    monkeypatch.setattr(dbmod, "_sleep", sleeps.append)
    r = rec_with(FlakyPg(), tmp_path)
    g = gate_for(r, alerts)
    assert g.check()                                    # DB fine at cycle start
    r._psycopg2 = FlakyPg(forever=True)
    r.on_noncritical_failure = warned.append
    r.record_decision(symbol="AAPL", decision="scan")
    r.record_heartbeat(status="running", scheduler_status="scheduled", dry_run=False)
    assert len(r.journal.pending()) == 2
    assert g.entries_allowed() and g.state == HEALTHY   # trading continues
    assert len(warned) == 2 and not r.critical_failed
    assert sum(sleeps) <= Recorder.RETRY_BUDGET_PER_CYCLE_S


def test_retry_budget_is_per_cycle(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(dbmod, "_sleep", sleeps.append)
    r = rec_with(FlakyPg(forever=True), tmp_path)
    r.begin_cycle()
    for _ in range(5):
        r.record_decision(symbol="X", decision="scan")
    assert sum(sleeps) <= 5.0                            # whole cycle, not per write
    first = len(sleeps)
    r.begin_cycle()
    r.record_decision(symbol="X", decision="scan")
    assert len(sleeps) > first                           # new cycle, new budget


def test_critical_failure_blocks_entries_until_reconciled(tmp_path):
    alerts = []
    r = rec_with(FlakyPg(), tmp_path)
    g = gate_for(r, alerts)
    assert g.check()
    r._psycopg2 = FlakyPg(forever=True)
    r.record_trade(action="buy", symbol="AAPL", qty=1, price=10.0, notional=10.0, sentiment=None,
                   stop_price=9.0, take_profit=15.0, reason="x", rationale="", dry_run=False,
                   order_id="o1", status="accepted")
    assert r.critical_failed
    assert not g.entries_allowed() and g.state == DOWN
    assert alerts and alerts[0][1] == "critical"
    pend = r.journal.pending()
    assert pend[0]["payload"]["critical"] is True
    r._psycopg2 = FlakyPg()                               # DB back
    assert g.check() and not r.critical_failed and g.state == HEALTHY


def test_critical_failure_detected_at_next_check_even_if_db_answers(tmp_path):
    alerts = []
    r = rec_with(FlakyPg(), tmp_path)
    calls = []
    g = DbHealthGate(r, alert=lambda t, s: alerts.append(s),
                     reconcile=lambda: (calls.append(1) or True, "ok"))
    assert g.check() and len(calls) == 1
    r._note_critical_failure(Exception("peak write failed"))
    assert g.check() and len(calls) == 2                 # reconciled again before entries


def test_peak_write_failure_is_critical(tmp_path):
    r = rec_with(FlakyPg(forever=True), tmp_path)
    r.update_position_peak("AAPL", 101.0)
    assert r.critical_failed


def test_closing_housekeeping_is_not_critical(tmp_path):
    r = rec_with(FlakyPg(forever=True), tmp_path)
    r.clear_position_peak("AAPL")
    assert not r.critical_failed
