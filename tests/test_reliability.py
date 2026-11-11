"""Stage 2 reliability: DB health gate, local journal, recovery
reconciliation, cycle watchdog, fail-closed protective-order check and the
entry block. No network, broker or real database."""
import json
import os
import sys
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.notifications.service import NotificationService  # noqa: E402
from bot.persistence.db import Recorder  # noqa: E402
from bot.reliability.db_gate import DOWN, HEALTHY, RECONCILING, DbHealthGate  # noqa: E402
from bot.reliability.journal import Journal  # noqa: E402
from bot.reliability.reconcile import make_reconciler  # noqa: E402
from bot.reliability.watchdog import CycleWatchdog  # noqa: E402
from bot.trading.strategy import CycleStats, SentimentStrategy  # noqa: E402


# ---- fakes -------------------------------------------------------------------
class _Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class _Alerts(list):
    def __call__(self, text, severity):
        self.append((severity, text))


class _GateRecorder:
    def __init__(self, ok=True):
        self.enabled = True
        self.ok = ok
        self.healthy = True
        self.last_error = None

    def health_check(self):
        self.healthy = self.ok
        self.last_error = None if self.ok else "password authentication failed"
        return self.ok


def _gate(ok=True, reconcile=(True, "3 positions held")):
    rec, alerts, clock = _GateRecorder(ok), _Alerts(), _Clock()
    calls = []

    def _reconcile():
        calls.append(1)
        return reconcile() if callable(reconcile) else reconcile

    return DbHealthGate(rec, alert=alerts, reconcile=_reconcile, clock=clock), rec, alerts, clock, calls


# ---- DB health gate ------------------------------------------------------------
def test_gate_starts_blocked_until_reconciled():
    gate, _, alerts, _, calls = _gate()
    assert gate.state == RECONCILING
    assert gate.entries_allowed() is False          # nothing allowed before the first check
    assert gate.check() is True                      # healthy DB + successful reconciliation
    assert gate.state == HEALTHY and calls == [1]
    assert alerts == []                              # a normal start-up is silent


def test_gate_blocks_and_alerts_immediately_on_failure_then_hourly():
    gate, rec, alerts, clock, _ = _gate()
    gate.check()
    rec.ok = False
    assert gate.check() is False
    assert gate.state == DOWN
    assert len(alerts) == 1 and alerts[0][0] == "critical"
    assert "BLOCKED" in alerts[0][1] and "untouched" in alerts[0][1]
    clock.t += 30 * 60
    gate.check()
    assert len(alerts) == 1                          # no spam within the hour
    clock.t += 31 * 60
    gate.check()
    assert len(alerts) == 2 and "still unreachable" in alerts[1][1]


def test_gate_recovery_requires_reconciliation_before_entries_resume():
    results = iter([(True, "start"), (False, "could not read broker protective orders"),
                    (True, "2 positions held")])
    gate, rec, alerts, clock, calls = _gate(reconcile=lambda: next(results))
    gate.check()
    rec.ok = False
    gate.check()
    rec.ok = True
    clock.t += 600
    assert gate.check() is False                     # DB back, but reconciliation failed
    assert gate.state == RECONCILING
    assert any("reconciliation FAILED" in a[1] for a in alerts)
    assert gate.check() is True                      # next cycle reconciles
    assert gate.state == HEALTHY
    assert "re-enabled" in alerts[-1][1] and alerts[-1][0] == "warning"


def test_gate_reconcile_exception_keeps_entries_blocked():
    def boom():
        raise RuntimeError("alpaca 500")
    gate, _, alerts, _, _ = _gate(reconcile=boom)
    assert gate.check() is False
    assert gate.state == RECONCILING


def test_gate_mid_cycle_write_failure_blocks_further_entries():
    gate, rec, alerts, _, _ = _gate()
    gate.check()
    rec.healthy, rec.last_error = False, "connection reset"
    assert gate.entries_allowed() is False
    assert gate.state == DOWN and alerts[0][0] == "critical"


def test_gate_is_noop_without_database():
    rec = SimpleNamespace(enabled=False)
    gate = DbHealthGate(rec, alert=_Alerts())
    assert gate.check() is True and gate.entries_allowed() is True


def test_gate_alert_failure_never_raises():
    def bad_alert(text, severity):
        raise ConnectionError("telegram down")
    rec = _GateRecorder(ok=False)
    gate = DbHealthGate(rec, alert=bad_alert)
    assert gate.check() is False                     # still blocks, doesn't crash


# ---- journal -------------------------------------------------------------------
def test_journal_append_pending_and_mark(tmp_path):
    j = Journal(str(tmp_path))
    a = j.append("db_write", {"statement": "INSERT INTO decisions", "params": {"x": datetime(2026, 1, 1)}})
    b = j.append("db_write", {"statement": "INSERT INTO trades"})
    assert [e["id"] for e in j.pending()] == [a, b]
    j.mark_replayed([a])
    assert [e["id"] for e in j.pending()] == [b]


def test_journal_skips_torn_line(tmp_path):
    j = Journal(str(tmp_path))
    j.append("db_write", {"n": 1})
    with open(j.path, "a") as fh:
        fh.write('{"id": "broken')                    # crash mid-write
    assert len(j.pending()) == 1


class _FailingPsycopg:
    def connect(self, *a, **k):
        raise Exception("FATAL: password authentication failed for user postgres")


def test_recorder_journals_failed_writes_instead_of_dropping(tmp_path):
    rec = Recorder(database_url="postgres://x")
    rec.enabled, rec._psycopg2 = True, _FailingPsycopg()
    rec.journal = Journal(str(tmp_path))
    rec.record_decision(symbol="AAPL", decision="scan", sentiment_score=-6.0)
    assert rec.healthy is False
    pend = rec.journal.pending()
    assert len(pend) == 1
    assert "INSERT INTO decisions" in pend[0]["payload"]["statement"]
    assert pend[0]["payload"]["params"]["symbol"] == "AAPL"
    assert rec.health_check() is False


class _RecordingCursor:
    def __init__(self, store, missing=()):
        self.store, self.missing = store, missing

    def execute(self, sql, params=None):
        self.store.append((sql, params))
        self._last = sql

    def fetchone(self):
        # Answer the health check's to_regclass() query: None = table missing.
        from bot.persistence.db import REQUIRED_TABLES
        return tuple(None if t in self.missing else t for t in REQUIRED_TABLES)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _OkConn:
    def __init__(self, store, missing=()):
        self.store, self.missing = store, missing

    def cursor(self):
        return _RecordingCursor(self.store, self.missing)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _OkPsycopg:
    def __init__(self, missing=()):
        self.store, self.missing = [], missing

    def connect(self, *a, **k):
        return _OkConn(self.store, self.missing)


def test_replay_is_idempotent_and_keeps_original_timestamp(tmp_path):
    rec = Recorder(database_url="postgres://x")
    pg = _OkPsycopg()
    rec.enabled, rec._psycopg2 = True, pg
    entries = [{"id": "11111111-1111-1111-1111-111111111111", "ts": "2026-10-22T14:00:00+00:00",
                "kind": "db_write", "payload": {"statement": "INSERT INTO decisions"}}]
    assert rec.replay_journal(entries) == 1
    sql, params = pg.store[-1]
    assert "ON CONFLICT (id) DO NOTHING" in sql
    assert params["ts"] == "2026-10-22T14:00:00+00:00"
    assert json.loads(params["payload"])["statement"] == "INSERT INTO decisions"
    assert rec.health_check() is True


# ---- reconciliation ---------------------------------------------------------------
class _ReconBroker:
    def __init__(self, positions=("AAPL", "MSFT"), protected=("AAPL", "MSFT")):
        self.positions = {s: SimpleNamespace(avg_entry_price=100.0) for s in positions}
        self.protected = None if protected is None else set(protected)

    def open_positions_detailed(self):
        return self.positions

    def get_protected_symbols(self):
        return self.protected


class _ReconRecorder:
    def __init__(self, replay_ok=True, rows=("AAPL", "MSFT")):
        self.replay_ok, self.rows = replay_ok, set(rows)

    def replay_journal(self, entries):
        return len(entries) if self.replay_ok else None

    def get_last_buy_trade(self, symbol):
        return {"price": 1.0} if symbol in self.rows else None


def test_reconcile_ok_and_marks_journal(tmp_path):
    j = Journal(str(tmp_path))
    j.append("db_write", {"n": 1})
    ok, summary = make_reconciler(_ReconRecorder(), _ReconBroker(), j)()
    assert ok and "1 journal entries copied" in summary and "2 positions held" in summary
    assert j.pending() == []


def test_reconcile_fails_when_journal_replay_fails(tmp_path):
    j = Journal(str(tmp_path))
    j.append("db_write", {"n": 1})
    ok, summary = make_reconciler(_ReconRecorder(replay_ok=False), _ReconBroker(), j)()
    assert not ok and "journal replay incomplete" in summary
    assert len(j.pending()) == 1                     # nothing lost


def test_reconcile_fails_closed_when_protective_orders_unreadable():
    ok, summary = make_reconciler(_ReconRecorder(), _ReconBroker(protected=None), None)()
    assert not ok and "protective orders" in summary


def test_reconcile_reports_unprotected_and_missing_rows():
    ok, summary = make_reconciler(_ReconRecorder(rows=("AAPL",)),
                                  _ReconBroker(protected=("AAPL",)), None)()
    assert ok
    assert "UNPROTECTED" in summary and "MSFT" in summary and "no DB buy row" in summary


# ---- watchdog --------------------------------------------------------------------------
NY = ZoneInfo("America/New_York")


def _watchdog(local_time):
    alerts, clock = _Alerts(), _Clock()
    w = CycleWatchdog(alert=alerts, clock=clock, now_local=lambda: local_time)
    return w, alerts, clock


def test_watchdog_alerts_after_gap_in_market_hours_and_on_resume():
    w, alerts, clock = _watchdog(datetime(2026, 11, 3, 11, 0, tzinfo=NY))   # Tuesday 11:00
    w.mark()
    clock.t += 40 * 60
    w.check_once()
    assert alerts == []
    clock.t += 30 * 60                                 # 70 minutes since the last cycle
    w.check_once()
    assert len(alerts) == 1 and alerts[0][0] == "critical"
    w.check_once()
    assert len(alerts) == 1                            # no repeat within the hour
    w.mark()
    assert "resumed" in alerts[-1][1]


def test_watchdog_silent_outside_market_hours():
    w, alerts, clock = _watchdog(datetime(2026, 11, 7, 11, 0, tzinfo=NY))   # Saturday
    clock.t += 10 * 3600
    w.check_once()
    assert alerts == []


# ---- fail-closed protective-order status -------------------------------------------------
def test_adoption_skips_when_protective_status_unknown():
    submitted = []

    class _Broker:
        def open_positions_detailed(self):
            return {"AAPL": SimpleNamespace(avg_entry_price=100.0, qty=10)}

        def get_protected_symbols(self):
            return None                                  # API error -> unknown

        def submit_protective_exit(self, *a, **k):
            submitted.append(a)

    fake = SimpleNamespace(broker=_Broker(), recorder=SimpleNamespace(get_last_buy_trade=lambda s: None),
                           cfg=None, _protected_this_cycle={"OLD"})
    SentimentStrategy._adopt_legacy_positions(fake)
    assert submitted == []
    assert fake._protected_this_cycle == set()


# ---- entry block --------------------------------------------------------------------------
def test_do_buy_blocked_when_db_gate_says_no():
    class _Risk:
        def evaluate(self, *a, **k):
            raise AssertionError("risk/broker must not be reached when the DB gate blocks")

    gate = SimpleNamespace(entries_allowed=lambda: False, last_reason="database unreachable")
    fake = SimpleNamespace(db_gate=gate, risk=_Risk())
    stats = CycleStats()
    out = SentimentStrategy._do_buy(fake, "AAPL", None, 100.0, {}, 5000.0, {}, {}, stats)
    assert out == 5000.0 and stats.blocked == 1


# ---- notification service defers to the gate ----------------------------------------------
def test_notification_service_does_not_double_alert_when_gate_attached():
    sent = []
    rec = SimpleNamespace(enabled=True, healthy=False, last_error="down",
                          record_decision=lambda **k: None, database_url=None)
    svc = NotificationService(rec, telegram=SimpleNamespace(send=lambda t, severity="info": sent.append(t)))
    svc.external_db_alerts = True
    svc.record_decision(symbol="X", decision="scan")
    assert sent == []
    svc.external_db_alerts = False
    svc.record_decision(symbol="X", decision="scan")
    assert len(sent) == 1 and "Trading continues unaffected" not in sent[0]


def test_startup_reconcile_failure_then_success_announces_reenable_without_outage_wording():
    results = iter([(False, "broker positions unreadable"), (True, "1 position held")])
    gate, _, alerts, clock, _ = _gate(reconcile=lambda: next(results))
    assert gate.check() is False
    clock.t += 1800
    assert gate.check() is True
    assert alerts[-1][1].startswith("Reconciliation complete") and "re-enabled" in alerts[-1][1]


def test_health_check_fails_when_schema_not_applied():
    """Found in staging: a reachable but empty database passed the old
    connect + SELECT 1 check while every write failed."""
    rec = Recorder(database_url="postgres://x")
    rec.enabled, rec._psycopg2 = True, _OkPsycopg(missing=("decisions", "heartbeats"))
    assert rec.health_check() is False
    assert "schema not applied" in rec.last_error
    assert "decisions" in rec.last_error and "heartbeats" in rec.last_error
    gate = DbHealthGate(rec, alert=_Alerts())
    assert gate.check() is False and "schema not applied" in gate.last_reason


def test_reconcile_second_look_finds_hidden_stop():
    from bot.trading.alpaca_client import BrokerSnapshot

    b = _ReconBroker(positions=("VZ",), protected=())
    b.symbol_orders_snapshot = lambda s: BrokerSnapshot(True, [
        {"symbol": "VZ", "side": "sell", "status": "held", "type": "stop", "group_role": "stop_loss"}], "")
    ok, summary = make_reconciler(_ReconRecorder(rows=("VZ",)), b, None)()
    assert ok and "UNPROTECTED" not in summary and "stop found in order history: VZ" in summary
    b.symbol_orders_snapshot = lambda s: BrokerSnapshot(True, [], "")
    ok, summary = make_reconciler(_ReconRecorder(rows=("VZ",)), b, None)()
    assert "UNPROTECTED (adoption will re-protect): VZ" in summary
