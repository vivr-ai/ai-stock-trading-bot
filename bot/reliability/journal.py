"""Append-only local journal (Reliability plan v3, 2F).

When Postgres can't be written, the Recorder appends what it was trying to
write here (one JSON object per line, flushed and fsynced) instead of
dropping it. On recovery, entries are copied into the `journal_events` table
with their ORIGINAL timestamps. Each entry has a UUID primary key, so
replaying twice never duplicates anything.

Journal entries are NOT re-executed against business tables (trades,
decisions, ...): replaying old INSERTs would stamp them with the recovery
time and could double-count. Trade history is rebuilt from Alpaca fills
(Stage 3); the journal keeps the bot's own record of what it decided while
the DB was down.

Durability: point JOURNAL_DIR at a Railway volume (e.g. /data/journal) and
the journal survives redeploys as well as restarts. Without a volume it
lives on the container's ephemeral disk (survives a restart, not a
redeploy); `durability()` reports which, and main.py alerts in staging.

Storage policy
  * The live file `db_journal.jsonl` rotates to `db_journal-<UTC time>.jsonl`
    at JOURNAL_ROTATE_MB (default 5 MB) or at the first write of a new UTC day.
  * A rotated segment is deleted only when EVERY entry in it has been
    replayed into Postgres AND it is older than JOURNAL_RETENTION_DAYS
    (default 30). Un-replayed entries are never deleted.
  * Hard cap JOURNAL_MAX_MB (default 200 MB) across all files. At the cap the
    oldest fully-replayed segments are removed first; if that isn't enough,
    new appends are refused and `on_problem` raises an alert. Trading,
    including exits, never waits on the journal.

Nothing here raises to the caller: a journal failure is reported through
`on_problem(key, text)` and `append()` returns None.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

LIVE_NAME = "db_journal.jsonl"
MARKER_NAME = "db_journal.replayed"
SEGMENT_PREFIX = "db_journal-"


def _fsync_dir(path: str) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class Journal:
    def __init__(self, directory: str, rotate_bytes: int = 5 * 1024 * 1024,
                 max_bytes: int = 200 * 1024 * 1024, retention_days: float = 30,
                 on_problem: Optional[Callable[[str, str], None]] = None,
                 clock: Callable[[], float] = time.time):
        self.directory = directory
        self.path = os.path.join(directory, LIVE_NAME)
        self.marker_path = os.path.join(directory, MARKER_NAME)
        self.rotate_bytes = int(rotate_bytes)
        self.max_bytes = int(max_bytes)
        self.retention_s = float(retention_days) * 86400.0
        self.on_problem = on_problem or (lambda key, text: None)
        self.clock = clock
        self._lock = threading.RLock()
        self.last_error: Optional[str] = None
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            self._problem("journal_unwritable", f"Journal directory {directory} unusable: {exc}")

    # ---- writing ----------------------------------------------------------
    def append(self, kind: str, payload: dict) -> Optional[str]:
        """Append one entry; returns its id, or None if it could not be made
        durable (reported via on_problem, never raised)."""
        entry = {"id": str(uuid.uuid4()),
                 "ts": datetime.fromtimestamp(self.clock(), tz=timezone.utc).isoformat(),
                 "kind": kind, "payload": payload}
        try:
            line = json.dumps(entry, default=str) + "\n"
        except (TypeError, ValueError) as exc:
            self._problem("journal_encode", f"Journal entry ({kind}) could not be encoded: {exc}")
            return None
        with self._lock:
            try:
                self._maybe_rotate()
                if self._total_bytes() + len(line) > self.max_bytes:
                    self.cleanup(force=True)
                    if self._total_bytes() + len(line) > self.max_bytes:
                        self._problem("journal_full",
                                      f"Journal is at its {self.max_bytes // (1024 * 1024)} MB cap with "
                                      "un-replayed entries; new entries are being dropped. Trading "
                                      "continues; restore the database so the journal can drain.")
                        return None
                created = not os.path.exists(self.path)
                if not created and not self._ends_with_newline(self.path):
                    # A crash mid-write left a torn last line; start a fresh
                    # line so this entry isn't glued onto the fragment.
                    line = "\n" + line
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line)
                    fh.flush()
                    os.fsync(fh.fileno())
                if created:
                    _fsync_dir(self.directory)
                self.last_error = None
                return entry["id"]
            except Exception as exc:  # noqa: BLE001
                self._problem("journal_unwritable", f"Journal write failed ({kind}): {exc}")
                return None

    def _maybe_rotate(self) -> None:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return
        now = self.clock()
        new_day = (datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).date()
                   != datetime.fromtimestamp(now, tz=timezone.utc).date())
        if st.st_size < self.rotate_bytes and not new_day:
            return
        if st.st_size == 0:
            return
        stamp = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        seq = 0
        while True:   # never overwrite an existing segment (same-instant rotations)
            target = os.path.join(self.directory, f"{SEGMENT_PREFIX}{stamp}-{seq:04d}.jsonl")
            if not os.path.exists(target):
                break
            seq += 1
        os.replace(self.path, target)
        _fsync_dir(self.directory)

    @staticmethod
    def _ends_with_newline(path: str) -> bool:
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                if fh.tell() == 0:
                    return True
                fh.seek(-1, os.SEEK_END)
                return fh.read(1) == b"\n"
        except OSError:
            return True

    # ---- reading / replay ---------------------------------------------------
    def _files(self) -> List[str]:
        """Rotated segments oldest first, then the live file."""
        try:
            names = os.listdir(self.directory)
        except OSError:
            return []
        segs = sorted(n for n in names if n.startswith(SEGMENT_PREFIX) and n.endswith(".jsonl"))
        out = [os.path.join(self.directory, n) for n in segs]
        if LIVE_NAME in names:
            out.append(self.path)
        return out

    def _total_bytes(self) -> int:
        total = 0
        for p in self._files() + [self.marker_path]:
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
        return total

    @staticmethod
    def _read_entries(path: str) -> Iterable[dict]:
        try:
            with open(path, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except ValueError:
                        # e.g. a torn last line after a crash mid-write
                        logger.warning("Journal %s line %d unreadable; skipped",
                                       os.path.basename(path), n)
        except FileNotFoundError:
            return

    def _replayed_ids(self) -> set:
        try:
            with open(self.marker_path, encoding="utf-8") as fh:
                return {line.strip() for line in fh if line.strip()}
        except FileNotFoundError:
            return set()

    def pending(self) -> List[dict]:
        """Entries not yet copied to Postgres, oldest first."""
        with self._lock:
            done = self._replayed_ids()
            out = []
            for p in self._files():
                out.extend(e for e in self._read_entries(p) if e.get("id") not in done)
            return out

    def mark_replayed(self, ids: List[str]) -> None:
        ids = [i for i in ids if i]
        if not ids:
            return
        with self._lock:
            with open(self.marker_path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(ids) + "\n")
                fh.flush()
                os.fsync(fh.fileno())

    def __len__(self) -> int:
        return len(self.pending())

    # ---- retention ------------------------------------------------------------
    def cleanup(self, force: bool = False) -> int:
        """Delete rotated segments whose entries are ALL replayed and that are
        older than the retention period (or, with force=True at the size cap,
        any fully-replayed segment, oldest first). Compacts the replayed-ids
        marker afterwards. Returns the number of segments deleted."""
        with self._lock:
            done = self._replayed_ids()
            now = self.clock()
            deleted = 0
            for p in self._files():
                if p == self.path:
                    continue
                ids = [e.get("id") for e in self._read_entries(p)]
                if any(i not in done for i in ids):
                    continue  # un-replayed entries are never deleted
                try:
                    age = now - os.path.getmtime(p)
                except OSError:
                    continue
                if not force and age < self.retention_s:
                    continue
                try:
                    os.remove(p)
                    deleted += 1
                except OSError as exc:
                    logger.warning("Journal cleanup could not delete %s: %s", p, exc)
            if deleted:
                _fsync_dir(self.directory)
                self._compact_marker()
            return deleted

    def _compact_marker(self) -> None:
        present = {e.get("id") for p in self._files() for e in self._read_entries(p)}
        keep = [i for i in self._replayed_ids() if i in present]
        tmp = self.marker_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(keep) + ("\n" if keep else ""))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.marker_path)
        _fsync_dir(self.directory)

    # ---- health -------------------------------------------------------------
    def self_test(self) -> Tuple[bool, str]:
        """Write, fsync and remove a probe file. Run at start-up."""
        probe = os.path.join(self.directory, ".probe")
        try:
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
                fh.flush()
                os.fsync(fh.fileno())
            os.remove(probe)
            return True, "writable"
        except Exception as exc:  # noqa: BLE001
            self._problem("journal_unwritable", f"Journal directory {self.directory} is not writable: {exc}")
            return False, str(exc)

    def durability(self, env: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
        """(durable, explanation). Durable = on a mounted Railway volume, so
        entries survive a redeploy."""
        env = os.environ if env is None else env
        mount = (env.get("RAILWAY_VOLUME_MOUNT_PATH") or "").rstrip("/")
        here = os.path.abspath(self.directory)
        if mount and (here == mount or here.startswith(mount + os.sep)):
            return True, f"on Railway volume {mount}"
        if env.get("RAILWAY_ENVIRONMENT") or env.get("RAILWAY_PROJECT_ID"):
            return False, ("on the container's ephemeral disk: survives a restart but NOT a redeploy "
                           "(set JOURNAL_DIR to a path on a Railway volume)")
        return True, "local disk (not on Railway)"

    def _problem(self, key: str, text: str) -> None:
        self.last_error = text
        logger.error("%s", text)
        try:
            self.on_problem(key, text)
        except Exception as exc:  # noqa: BLE001
            logger.error("Journal problem alert failed: %s", exc)
