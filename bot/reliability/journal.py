"""Local append-only journal for database writes that failed.

When Postgres can't be written, the Recorder appends what it was trying to
write here (one JSON object per line, flushed + fsynced) instead of dropping
it. On recovery, entries are copied into the `journal_events` table with their
ORIGINAL timestamps. Each entry has a UUID primary key, so replaying twice
never duplicates anything.

Journal entries are NOT re-executed against business tables (trades,
decisions, ...): replaying old INSERTs would stamp them with the recovery
time and could double-count. Trade history is rebuilt from Alpaca fills
instead (the broker is the source of truth - see the Stage 3 reconciliation);
the journal keeps the bot's own record of what it decided while the DB was
down.

Storage note: Railway's container disk is ephemeral. The journal survives a
process restart inside the same deployment, not a redeploy. Broker state
(positions, orders, fills) is unaffected by that and is what reconciliation
trusts.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Iterator, List, Optional

logger = logging.getLogger(__name__)


class Journal:
    def __init__(self, directory: str):
        self.directory = directory
        self.path = os.path.join(directory, "db_journal.jsonl")
        self.marker_path = os.path.join(directory, "db_journal.replayed")
        self._lock = threading.Lock()
        os.makedirs(directory, exist_ok=True)

    def append(self, kind: str, payload: dict) -> Optional[str]:
        """Append one entry; returns its id, or None if even the local disk
        write failed (logged, never raised - journaling must not break a
        trading cycle)."""
        entry = {"id": str(uuid.uuid4()), "ts": datetime.now(timezone.utc).isoformat(),
                 "kind": kind, "payload": payload}
        try:
            line = json.dumps(entry, default=str)
            with self._lock, open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            return entry["id"]
        except Exception as exc:  # noqa: BLE001
            logger.error("Journal append failed (%s): %s", kind, exc)
            return None

    def _replayed_ids(self) -> set:
        try:
            with open(self.marker_path, encoding="utf-8") as fh:
                return {line.strip() for line in fh if line.strip()}
        except FileNotFoundError:
            return set()

    def pending(self) -> List[dict]:
        """Entries not yet copied to Postgres, oldest first. Unreadable lines
        (e.g. a torn last line after a crash) are skipped and logged."""
        done = self._replayed_ids()
        out = []
        try:
            with open(self.path, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        logger.warning("Journal line %d unreadable; skipped", n)
                        continue
                    if entry.get("id") not in done:
                        out.append(entry)
        except FileNotFoundError:
            return []
        return out

    def mark_replayed(self, ids: List[str]) -> None:
        if not ids:
            return
        with self._lock, open(self.marker_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(ids) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def __len__(self) -> int:
        return len(self.pending())
