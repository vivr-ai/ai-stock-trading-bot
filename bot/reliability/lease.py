"""Single-instance trading lease (Reliability plan v3, 2I).

Only the process holding the lease may submit, replace or cancel orders. A
Railway volume makes overlapping deployments rarer but proves nothing; this
lease is the control. Layers 2 and 3 (deterministic order IDs and a broker
check before each order) cover what a lease can't see, e.g. a copy running
against a different database with the same Alpaca keys.

Record: one row per Alpaca account in `bot_lease` (see db/schema.sql), using
the DATABASE's clock for every time comparison.

  acquire   one INSERT ... ON CONFLICT DO UPDATE ... WHERE the current lease
            has expired (or is already mine). Postgres serialises the row, so
            two contenders can't both win. Every change of holder increments
            `epoch` (the fencing number).
  renew     every 20 s on a background thread: UPDATE ... WHERE holder = me AND
            epoch = mine, extending expiry to now + 90 s. Zero rows = LOST.
  freshness before ANY order: the last successful renewal must be under 60 s
            old (local monotonic clock, so wall-clock jumps don't matter).
  lose      renewal finds another holder or a new epoch -> all order
            submissions stop immediately; critical alert; standby.
  release   clean shutdown sets expiry to now, so a new deployment takes over
            at once. A crash simply lets the lease expire (90 s).
  takeover  a new holder is RECONCILING until the broker reconciliation
            (positions, open orders, journal replay) succeeds; only then HELD.

Database outage (approved policy D8): while the last confirmed renewal is
under 60 s old the holder may continue exits and protection (never entries:
the DB gate blocks those). Past that, NO order of any kind is submitted until
a renewal succeeds; the broker-resting bracket stops remain the protection.
If renewals were missed for longer than the lease's expiry, the instance
re-reconciles before trading again, because someone else could have held the
lease in the meantime. An instance never assumes ownership it can't confirm.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

DISABLED, STANDBY, RECONCILING, HELD, LOST = "disabled", "standby", "reconciling", "held", "lost"


class LeaseStoreError(RuntimeError):
    """The lease store could not be reached: ownership is UNKNOWN."""


def make_instance_id(env=None) -> str:
    env = os.environ if env is None else env
    dep = (env.get("RAILWAY_DEPLOYMENT_ID") or "local")[:12]
    return f"{dep}:{socket.gethostname()[:24]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class PgLeaseStore:
    """Postgres implementation. Each call opens its own short connection with
    a 5 s connect timeout and a 5 s statement timeout, and raises
    LeaseStoreError when the database can't be reached."""

    def __init__(self, database_url: str, psycopg2_module=None):
        self.database_url = database_url
        if psycopg2_module is None:
            import psycopg2 as psycopg2_module  # noqa: N813
        self._pg = psycopg2_module

    def _run(self, sql: str, params: dict):
        try:
            conn = self._pg.connect(self.database_url, connect_timeout=5,
                                    options="-c statement_timeout=5000")
        except Exception as exc:  # noqa: BLE001
            raise LeaseStoreError(str(exc)) from exc
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchone() if cur.description else None
        except Exception as exc:  # noqa: BLE001
            raise LeaseStoreError(str(exc)) from exc
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def acquire(self, name: str, holder: str, ttl_s: float) -> Optional[int]:
        row = self._run(
            """
            INSERT INTO bot_lease (name, holder, epoch, acquired_at, renewed_at, expires_at)
            VALUES (%(name)s, %(holder)s, 1, now(), now(), now() + make_interval(secs => %(ttl)s))
            ON CONFLICT (name) DO UPDATE SET
                holder = EXCLUDED.holder, epoch = bot_lease.epoch + 1,
                acquired_at = now(), renewed_at = now(), expires_at = EXCLUDED.expires_at
            WHERE bot_lease.expires_at < now() OR bot_lease.holder = EXCLUDED.holder
            RETURNING epoch
            """,
            dict(name=name, holder=holder, ttl=float(ttl_s)),
        )
        return int(row[0]) if row else None

    def renew(self, name: str, holder: str, epoch: int, ttl_s: float) -> bool:
        row = self._run(
            """
            UPDATE bot_lease SET renewed_at = now(), expires_at = now() + make_interval(secs => %(ttl)s)
            WHERE name = %(name)s AND holder = %(holder)s AND epoch = %(epoch)s
            RETURNING epoch
            """,
            dict(name=name, holder=holder, epoch=epoch, ttl=float(ttl_s)),
        )
        return row is not None

    def release(self, name: str, holder: str, epoch: int) -> bool:
        row = self._run(
            """
            UPDATE bot_lease SET expires_at = now()
            WHERE name = %(name)s AND holder = %(holder)s AND epoch = %(epoch)s
            RETURNING epoch
            """,
            dict(name=name, holder=holder, epoch=epoch),
        )
        return row is not None

    def current(self, name: str) -> Optional[dict]:
        row = self._run(
            "SELECT holder, epoch, expires_at > now() FROM bot_lease WHERE name = %(name)s",
            dict(name=name),
        )
        return None if row is None else {"holder": row[0], "epoch": int(row[1]), "valid": bool(row[2])}

    def log_event(self, name: str, holder: str, epoch: Optional[int], event: str, detail: str = "") -> None:
        try:
            self._run(
                """INSERT INTO bot_lease_events (name, holder, epoch, event, detail)
                   VALUES (%(name)s, %(holder)s, %(epoch)s, %(event)s, %(detail)s)""",
                dict(name=name, holder=holder, epoch=epoch, event=event, detail=detail[:500]),
            )
        except LeaseStoreError:
            pass  # evidence only; never affects the lease


class _NullAlerts:
    def raise_(self, *a, **k):
        return False

    def resolve(self, *a, **k):
        return False


class InstanceLease:
    def __init__(self, store, name: str, holder: Optional[str] = None,
                 reconcile: Optional[Callable[[], Tuple[bool, str]]] = None,
                 alerts=None,
                 ttl_s: float = 90.0, renew_every_s: float = 20.0, max_age_s: float = 60.0,
                 standby_alert_after_s: float = 600.0,
                 monotonic: Callable[[], float] = time.monotonic):
        self.store = store
        self.name = name
        self.holder = holder or make_instance_id()
        self.reconcile = reconcile or (lambda: (True, "no reconciliation configured"))
        self.alerts = alerts or _NullAlerts()
        self.ttl_s, self.renew_every_s, self.max_age_s = ttl_s, renew_every_s, max_age_s
        self.standby_alert_after_s = standby_alert_after_s
        self.mono = monotonic
        self.state = STANDBY
        self.epoch: Optional[int] = None
        self.last_ok: Optional[float] = None       # monotonic time of last confirmed acquire/renew
        self.standby_since = self.mono()
        self.last_reason = "not yet acquired"
        self._lock = threading.RLock()
        self._stop = threading.Event()

    # ---- queries -------------------------------------------------------------
    def age(self) -> Optional[float]:
        return None if self.last_ok is None else self.mono() - self.last_ok

    def fresh(self) -> bool:
        a = self.age()
        return a is not None and a < self.max_age_s

    def can_submit(self, kind: str = "order") -> Tuple[bool, str]:
        """The pre-order check. True only while this instance holds the
        lease, has reconciled since acquiring it, and renewed it within the
        last 60 s."""
        with self._lock:
            if self.state != HELD:
                return False, f"trading lease not held ({self.state}: {self.last_reason})"
            if not self.fresh():
                a = self.age()
                return False, (f"trading lease not confirmed for {a:.0f}s (limit {self.max_age_s:.0f}s); "
                               "order submissions paused")
            return True, f"lease held (epoch {self.epoch})"

    def entry_blocker(self) -> Tuple[bool, str]:
        ok, why = self.can_submit("entry")
        return (not ok), why

    # ---- transitions ---------------------------------------------------------
    def try_acquire(self) -> bool:
        with self._lock:
            try:
                epoch = self.store.acquire(self.name, self.holder, self.ttl_s)
            except LeaseStoreError as exc:
                self.last_reason = f"lease store unreachable: {exc}"
                logger.warning("Lease acquire failed: %s", exc)
                return False
            if epoch is None:
                self.last_reason = "another instance holds the trading lease"
                self._maybe_standby_alert()
                return False
            self.epoch, self.last_ok, self.state = epoch, self.mono(), RECONCILING
            self.last_reason = "acquired; broker reconciliation pending"
            logger.warning("Trading lease ACQUIRED (epoch %s, instance %s); reconciling before trading.",
                           epoch, self.holder)
            self.store.log_event(self.name, self.holder, epoch, "acquired")
            self.alerts.resolve("lease_standby", "Trading lease acquired; this instance is now active.")
            return True

    def renew(self) -> None:
        with self._lock:
            if self.state not in (HELD, RECONCILING) or self.epoch is None:
                return
            before = self.age()
            try:
                ok = self.store.renew(self.name, self.holder, self.epoch, self.ttl_s)
            except LeaseStoreError as exc:
                a = self.age() or 0.0
                self.last_reason = f"lease renewal failing for {a:.0f}s: {exc}"
                # DB outage drill 12 Nov: two callers (renew thread + the
                # protection hook) logged this ~4 times a minute for 45 min.
                # Log the first failure, then at most once a minute.
                now_m = self.mono()
                if now_m - getattr(self, "_last_fail_log", -1e9) >= 60.0:
                    self._last_fail_log = now_m
                    logger.warning("Lease renewal failed (%.0fs since last confirmation): %s", a, exc)
                if a >= self.max_age_s:
                    self.alerts.raise_("lease_unverified",
                                       "TRADING LEASE CANNOT BE CONFIRMED (database unreachable). All "
                                       "order submissions are paused; broker-side stop orders remain in "
                                       f"place. Error: {exc}", "critical", 3600)
                return
            if not ok:
                self._lose("another instance now holds the trading lease")
                return
            self.last_ok = self.mono()
            if before is not None and before >= self.ttl_s and self.state == HELD:
                # Blind for longer than the lease lasts: someone else could
                # have traded meanwhile. Re-reconcile before trading again.
                self.state = RECONCILING
                self.last_reason = f"lease confirmed again after {before:.0f}s; re-reconciling"
                logger.warning("Lease renewed after a %.0fs gap; re-reconciling before trading.", before)
            elif self.state == HELD:
                self.last_reason = "held"
            self._last_fail_log = -1e9
            self.alerts.resolve("lease_unverified", "Trading lease confirmed again.")

    def _lose(self, why: str) -> None:
        old = self.epoch
        self.state, self.epoch, self.last_ok = LOST, None, None
        self.last_reason = why
        self.standby_since = self.mono()
        logger.error("Trading lease LOST (was epoch %s): %s. All order submissions stopped.", old, why)
        self.alerts.raise_("lease_lost", f"TRADING LEASE LOST: {why}. This instance has stopped "
                                         "submitting orders and is on standby.", "critical", 3600)
        self.store.log_event(self.name, self.holder, old, "lost", why)

    def ensure_ready(self) -> Tuple[bool, str]:
        """Called at the start of every trading cycle. Acquires if possible,
        renews if stale, and runs the takeover reconciliation."""
        with self._lock:
            if self.state in (STANDBY, LOST):
                if not self.try_acquire():
                    return False, f"standby: {self.last_reason}"
            if self.state in (HELD, RECONCILING) and not self.fresh():
                self.renew()
                if self.state == LOST:
                    return False, self.last_reason
                if not self.fresh():
                    return False, self.last_reason
            if self.state == RECONCILING:
                try:
                    ok, summary = self.reconcile()
                except Exception as exc:  # noqa: BLE001
                    ok, summary = False, f"reconciliation raised: {exc}"
                if not ok:
                    self.last_reason = f"takeover reconciliation failed: {summary}"
                    logger.error("Lease held but reconciliation failed (%s); not trading.", summary)
                    self.alerts.raise_("lease_reconcile", "Lease acquired but broker reconciliation "
                                                          f"failed: {summary}. Not trading.", "critical", 3600)
                    return False, self.last_reason
                self.state, self.last_reason = HELD, "held"
                logger.warning("Trading lease HELD (epoch %s): reconciliation complete (%s).",
                               self.epoch, summary)
                self.alerts.resolve("lease_reconcile", "Broker reconciliation succeeded; trading.")
                self.store.log_event(self.name, self.holder, self.epoch, "reconciled", summary)
            return self.can_submit("cycle")

    def release(self) -> None:
        with self._lock:
            if self.epoch is None:
                return
            try:
                self.store.release(self.name, self.holder, self.epoch)
                self.store.log_event(self.name, self.holder, self.epoch, "released")
                logger.info("Trading lease released (epoch %s).", self.epoch)
            except LeaseStoreError as exc:
                logger.warning("Lease release failed (it will expire on its own): %s", exc)
            self.state, self.epoch, self.last_ok = STANDBY, None, None

    def _maybe_standby_alert(self) -> None:
        waited = self.mono() - self.standby_since
        if waited >= self.standby_alert_after_s:
            self.alerts.raise_("lease_standby",
                               f"This instance has been on standby for {int(waited // 60)} min: another "
                               "instance holds the trading lease. If only one bot should be running, "
                               "check Railway for a second deployment or a copy running elsewhere.",
                               "critical", 3600)

    # ---- background renewal ----------------------------------------------------
    def tick(self) -> None:
        with self._lock:
            if self.state in (HELD, RECONCILING):
                self.renew()
            else:
                self.try_acquire()

    def start(self) -> None:
        def _loop():
            while not self._stop.wait(self.renew_every_s):
                try:
                    self.tick()
                except Exception as exc:  # noqa: BLE001
                    logger.error("Lease heartbeat error: %s", exc)
        threading.Thread(target=_loop, daemon=True, name="trading-lease").start()

    def stop(self) -> None:
        self._stop.set()
