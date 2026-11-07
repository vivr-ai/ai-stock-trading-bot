"""Reliability v3 2F: durable journal - rotation, retention, cap, restart and
redeploy recovery, idempotent replay with original timestamps."""
import json
import os
import stat

from bot.persistence.db import Recorder
from bot.reliability.journal import LIVE_NAME, SEGMENT_PREFIX, Journal
from bot.reliability.reconcile import make_reconciler

from tests.test_reliability import _FailingPsycopg, _OkPsycopg, _ReconBroker, _ReconRecorder

DAY = 86400.0


class Clock:
    def __init__(self, t=None):
        import time
        self.t = time.time() if t is None else t

    def __call__(self):
        return self.t


def segs(d):
    return sorted(n for n in os.listdir(d) if n.startswith(SEGMENT_PREFIX))


def test_rotates_at_size_and_keeps_all_entries_pending(tmp_path):
    j = Journal(str(tmp_path), rotate_bytes=300, clock=Clock())
    ids = [j.append("db_write", {"n": i, "pad": "x" * 100}) for i in range(6)]
    assert all(ids)
    assert len(segs(tmp_path)) >= 2
    assert [e["payload"]["n"] for e in j.pending()] == list(range(6))   # oldest first


def test_rotates_on_new_utc_day(tmp_path):
    c = Clock()
    j = Journal(str(tmp_path), clock=c)
    j.append("db_write", {"n": 1})
    os.utime(j.path, (c.t, c.t))
    c.t += DAY
    j.append("db_write", {"n": 2})
    assert len(segs(tmp_path)) == 1 and os.path.exists(j.path)
    assert [e["payload"]["n"] for e in j.pending()] == [1, 2]


def test_cleanup_never_deletes_unreplayed_and_respects_retention(tmp_path):
    c = Clock()
    j = Journal(str(tmp_path), rotate_bytes=50, retention_days=30, clock=c)
    a = j.append("db_write", {"n": 1, "pad": "x" * 60})
    j.append("db_write", {"n": 2, "pad": "x" * 60})      # rotates a's file out
    first = segs(tmp_path)[0]
    os.utime(os.path.join(tmp_path, first), (c.t, c.t))
    c.t += 31 * DAY
    assert j.cleanup() == 0                               # old but not replayed: kept
    j.mark_replayed([a])
    c.t -= 20 * DAY
    assert j.cleanup() == 0                               # replayed but only 11 days old
    c.t += 20 * DAY
    assert j.cleanup() == 1                               # replayed and > 30 days: deleted
    assert first not in segs(tmp_path)
    assert a not in open(j.marker_path).read()            # marker compacted
    assert [e["payload"]["n"] for e in j.pending()] == [2]


def test_cap_drops_new_entries_and_alerts_but_never_raises(tmp_path):
    problems = []
    j = Journal(str(tmp_path), max_bytes=400, on_problem=lambda k, t: problems.append(k))
    results = [j.append("db_write", {"pad": "y" * 80}) for _ in range(8)]
    assert results[0] is not None and results[-1] is None
    assert "journal_full" in problems
    assert len(j.pending()) == sum(1 for r in results if r)   # nothing written was lost


def test_cap_frees_space_by_removing_replayed_segments_first(tmp_path):
    j = Journal(str(tmp_path), rotate_bytes=150, max_bytes=1000)
    first = [j.append("db_write", {"pad": "z" * 80}) for _ in range(4)]
    assert all(first)
    j.mark_replayed(first)
    later = [j.append("db_write", {"pad": "z" * 80}) for _ in range(4)]
    assert all(later)


def test_write_failure_is_reported_not_raised(tmp_path):
    d = tmp_path / "ro"
    d.mkdir()
    problems = []
    j = Journal(str(d), on_problem=lambda k, t: problems.append(k))
    os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)
    try:
        if os.access(str(d), os.W_OK):   # running as root: permissions not enforced
            return
        assert j.append("db_write", {"n": 1}) is None
        assert "journal_unwritable" in problems
        ok, _ = j.self_test()
        assert not ok
    finally:
        os.chmod(d, stat.S_IRWXU)


def test_durability_detection():
    j = Journal("/data/journal")
    assert j.durability({"RAILWAY_VOLUME_MOUNT_PATH": "/data", "RAILWAY_ENVIRONMENT": "x"})[0]
    assert not j.durability({"RAILWAY_ENVIRONMENT": "x"})[0]
    assert not j.durability({"RAILWAY_VOLUME_MOUNT_PATH": "/other", "RAILWAY_PROJECT_ID": "p"})[0]
    assert j.durability({})[0]          # local development


def test_restart_reads_torn_last_line_and_keeps_going(tmp_path):
    j = Journal(str(tmp_path))
    j.append("db_write", {"n": 1})
    with open(j.path, "a") as fh:
        fh.write('{"id": "half-writt')          # crash mid-write
    j2 = Journal(str(tmp_path))                    # process restart
    j2.append("db_write", {"n": 2})
    assert [e["payload"]["n"] for e in j2.pending()] == [1, 2]


def test_redeploy_during_db_outage_replays_once_with_original_timestamps(tmp_path):
    vol = str(tmp_path / "volume" / "journal")
    # Deployment 1: DB down -> writes journalled on the volume.
    c = Clock()
    rec1 = Recorder(database_url="postgres://x")
    rec1.enabled, rec1._psycopg2 = True, _FailingPsycopg()
    rec1.journal = Journal(vol, clock=c)
    rec1.record_decision(symbol="AAPL", decision="scan")
    original = [e["ts"] for e in rec1.journal.pending()]
    assert len(original) == 1
    # Redeploy: new process, same volume, DB back.
    c.t += 3600
    rec2 = Recorder(database_url="postgres://x")
    pg = _OkPsycopg()
    rec2.enabled, rec2._psycopg2 = True, pg
    j2 = Journal(vol, clock=c)
    ok, summary = make_reconciler(rec2, _ReconBroker(), j2)()
    assert ok and "1 journal entries copied" in summary
    replayed = [p for sql, p in pg.store if "journal_events" in sql]
    assert [p["ts"] for p in replayed] == original          # original timestamp kept
    # A second reconciliation (e.g. another restart) replays nothing.
    ok, summary = make_reconciler(rec2, _ReconBroker(), Journal(vol, clock=c))()
    assert ok and "journal entries copied" not in summary
    assert len([1 for sql, _ in pg.store if "journal_events" in sql]) == 1


def test_replay_failure_keeps_entries_for_next_attempt(tmp_path):
    j = Journal(str(tmp_path))
    j.append("db_write", {"n": 1})
    ok, _ = make_reconciler(_ReconRecorder(replay_ok=False), _ReconBroker(), j)()
    assert not ok and len(Journal(str(tmp_path)).pending()) == 1


def test_entries_are_valid_json_lines(tmp_path):
    j = Journal(str(tmp_path))
    j.append("db_write", {"price": 1.5})
    with open(os.path.join(tmp_path, LIVE_NAME)) as fh:
        row = json.loads(fh.readline())
    assert set(row) == {"id", "ts", "kind", "payload"} and row["ts"].endswith("+00:00")
