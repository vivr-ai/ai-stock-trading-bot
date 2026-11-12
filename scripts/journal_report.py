"""Read-only evidence for the recorder-only DB outage drill (review 13 Nov item 3).

Prints one 'JOURNAL_REPORT {json}' line with:
  * the journal files on the volume (JOURNAL_DIR): segments, sizes, entries,
    how many are marked replayed and how many are still pending;
  * journal_events rows in Postgres journaled in the drill window
    (--since ISO time), by kind, and any duplicated ids (must be none);
  * entries journalled on disk in the window that are NOT in Postgres.
Reads only. Refuses to run unless BOT_ENVIRONMENT=staging.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    if (os.environ.get("BOT_ENVIRONMENT") or "").lower() != "staging":
        print("JOURNAL_REPORT refused: staging only", flush=True)
        return 2
    since = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--since=")), None)
    from bot.reliability.journal import Journal

    jdir = os.environ.get("JOURNAL_DIR", "logs/journal")
    j = Journal(jdir)
    files = sorted(n for n in os.listdir(jdir)) if os.path.isdir(jdir) else []
    all_entries = []
    for name in files:
        if not name.endswith(".jsonl") or not name.startswith("db_journal"):
            continue
        with open(os.path.join(jdir, name)) as fh:
            for line in fh:
                try:
                    all_entries.append(json.loads(line))
                except ValueError:
                    pass
    window = [e for e in all_entries if not since or e.get("ts", "") >= since]
    pending = j.pending()
    report = {"journal_dir": jdir, "files": [(n, os.path.getsize(os.path.join(jdir, n))) for n in files],
              "entries_total": len(all_entries), "entries_in_window": len(window),
              "pending_not_replayed": len(pending),
              "window_kinds": _count(e.get("kind") for e in window)}
    try:
        import psycopg2

        with psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10) as c, c.cursor() as cur:
            cur.execute("SELECT id::text, kind, journaled_at, replayed_at FROM journal_events "
                        "WHERE journaled_at >= COALESCE(%s::timestamptz, '-infinity') ORDER BY journaled_at",
                        (since,))
            rows = cur.fetchall()
            cur.execute("SELECT count(*) - count(DISTINCT id) FROM journal_events")
            dupes = cur.fetchone()[0]
        db_ids = {r[0] for r in rows}
        report.update(db_rows_in_window=len(rows), db_kinds=_count(r[1] for r in rows),
                      db_duplicate_ids=dupes,
                      first_journaled=str(rows[0][2]) if rows else None,
                      last_replayed=str(max(r[3] for r in rows)) if rows else None,
                      on_disk_not_in_db=[e["id"] for e in window if e.get("id") not in db_ids][:20])
    except Exception as exc:  # noqa: BLE001
        report["db_error"] = str(exc)
    print("JOURNAL_REPORT " + json.dumps(report, default=str), flush=True)
    return 0


def _count(items):
    out = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


if __name__ == "__main__":
    sys.exit(main())
