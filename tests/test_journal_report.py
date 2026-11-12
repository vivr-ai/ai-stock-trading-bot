"""journal_report: staging-only, read-only evidence for the recorder DB drill."""
import importlib.util
import json
import pathlib
import sys

from tests.pg_fixture import pg_url  # noqa: F401 - fixture

ROOT = pathlib.Path(__file__).resolve().parents[1]
_s = importlib.util.spec_from_file_location("journal_report", ROOT / "scripts" / "journal_report.py")
jr = importlib.util.module_from_spec(_s)
sys.modules[_s.name] = jr
_s.loader.exec_module(jr)


def test_refuses_outside_staging(monkeypatch, capsys):
    monkeypatch.setenv("BOT_ENVIRONMENT", "production")
    assert jr.main() == 2 and "refused" in capsys.readouterr().out


def test_reports_disk_entries_and_db_rows(monkeypatch, capsys, tmp_path, pg_url):
    from bot.persistence.db import Recorder
    from bot.reliability.journal import Journal

    import psycopg2
    with psycopg2.connect(pg_url) as c, c.cursor() as cur:
        cur.execute("TRUNCATE journal_events")
    j = Journal(str(tmp_path))
    a = j.append("db_write", {"n": 1})
    j.append("db_write", {"n": 2})
    rec = Recorder(database_url=pg_url)
    rec.replay_journal([e for e in j.pending() if e["id"] == a])
    monkeypatch.setenv("BOT_ENVIRONMENT", "staging")
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setattr(sys, "argv", ["journal_report.py"])
    assert jr.main() == 0
    rep = json.loads(capsys.readouterr().out.split("JOURNAL_REPORT ", 1)[1])
    assert rep["entries_total"] == 2 and rep["db_rows_in_window"] == 1 and rep["db_duplicate_ids"] == 0
    assert len(rep["on_disk_not_in_db"]) == 1
