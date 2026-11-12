"""Stage 6c: staging-only fault injection (Reliability plan v3).

STAGING_FAULTS is a comma-separated list of one-shot faults, e.g.
    STAGING_FAULTS=replace_reject:2,sell_drop_response:1,kill_at:EXIT_WORKING

Each fault fires the stated number of times (default 1) and is then spent.
The injector refuses to exist unless BOT_ENVIRONMENT=staging (or a test
passes `allow=True`): in production it can't be switched on, whatever the
variables say.

Faults
  replace_reject[:n]        replace -> OrderRejected (422 invalid price)    test 3
  replace_drop_response[:n] replace is SENT, then the response is lost      test 6A
  sell_reject[:n]           exit sell -> OrderRejected                      tests 11/14
  sell_drop_response[:n]    exit sell is SENT, then the response is lost    test 6A
  sell_lost[:n]             exit sell NOT sent, outcome unknown             test 6B
  restore_reject[:n]        protective OCO -> rejected                      test 14
  read_errors[:n]           positions/orders reads fail                     broker outage
  kill_at:<STATE>           process exits right after entering STATE       test 12
  db_outage:<start_min>-<end_min>
                            every NEW database connection (bot recorder AND
                            trading lease) fails with OperationalError from
                            <start_min> to <end_min> minutes after start-up.
                            The DB outage drill, with no Railway changes.
  db_outage_recorder:<start_min>-<end_min>
                            as db_outage, but ONLY the bot's recorder loses
                            the DB (the trading lease keeps working), so the
                            DB health gate, entry blocking and the local
                            journal + replay are exercised (12 Nov drill: with
                            the lease down too, D8 paused the whole cycle
                            before the gate ran, so those weren't reached).
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Dict, Optional

from ..trading.alpaca_client import OrderOutcomeUnknown, OrderRejected

logger = logging.getLogger(__name__)

KNOWN = {"replace_reject", "replace_drop_response", "sell_reject", "sell_drop_response", "sell_lost",
         "restore_reject", "read_errors", "kill_at", "db_outage", "db_outage_recorder"}


class FaultInjector:
    def __init__(self, spec: str, *, allow: bool = False, env: Optional[Dict[str, str]] = None,
                 kill: Callable[[int], None] = os._exit, on_fire: Callable[[str, str], None] = None):
        env = os.environ if env is None else env
        if not allow and (env.get("BOT_ENVIRONMENT") or "").lower() != "staging":
            raise RuntimeError("STAGING_FAULTS is only allowed when BOT_ENVIRONMENT=staging")
        self.counts: Dict[str, int] = {}
        self.kill_states = set()
        self.db_window = None
        self.db_recorder_only = False
        for part in filter(None, (p.strip() for p in (spec or "").split(","))):
            name, _, arg = part.partition(":")
            if name not in KNOWN:
                raise ValueError(f"unknown fault {name!r}; known: {sorted(KNOWN)}")
            if name == "kill_at":
                self.kill_states.add(arg)
            elif name in ("db_outage", "db_outage_recorder"):
                self.db_recorder_only = name == "db_outage_recorder"
                a, _, b = arg.partition("-")
                self.db_window = (float(a) * 60.0, float(b) * 60.0)
                if not 0 <= self.db_window[0] < self.db_window[1] <= 180 * 60:
                    raise ValueError("db_outage window must be within 0-180 minutes, start < end")
            else:
                self.counts[name] = self.counts.get(name, 0) + (int(arg) if arg else 1)
        self.kill = kill
        self.fired = []
        self.on_fire = on_fire or (lambda name, detail: None)

    def _take(self, name: str) -> bool:
        if self.counts.get(name, 0) > 0:
            self.counts[name] -= 1
            return True
        return False

    def _fire(self, name: str, detail: str) -> None:
        self.fired.append((name, detail))
        logger.warning("FAULT_INJECTED %s (%s)", name, detail)
        self.on_fire(name, detail)

    def wrap(self, broker):
        """Patch the broker instance's order and read methods in place."""
        inj = self
        orig_replace = getattr(broker, "replace_order_once", None)
        orig_submit = getattr(broker, "submit_order_once", None)
        orig_oco = getattr(broker, "submit_oco_once", None)
        orig_pos = getattr(broker, "positions_snapshot", None)

        def replace_order_once(order_id, limit_price, client_order_id, what):
            if inj._take("replace_reject"):
                inj._fire("replace_reject", client_order_id)
                raise OrderRejected("422 injected: invalid limit price", 422)
            res = orig_replace(order_id, limit_price, client_order_id, what)
            if inj._take("replace_drop_response"):
                inj._fire("replace_drop_response", client_order_id)
                raise OrderOutcomeUnknown("injected: response lost after the replace was sent")
            return res

        def submit_order_once(body, kind, what):
            cid = body.get("client_order_id")
            exit_sell = body.get("side") == "sell" and kind == "exit"
            if exit_sell and inj._take("sell_reject"):
                inj._fire("sell_reject", cid)
                raise OrderRejected("422 injected: sell rejected", 422)
            if exit_sell and inj._take("sell_lost"):
                inj._fire("sell_lost", cid)
                raise OrderOutcomeUnknown("injected: request lost, nothing sent")
            res = orig_submit(body, kind, what)
            if exit_sell and inj._take("sell_drop_response"):
                inj._fire("sell_drop_response", cid)
                raise OrderOutcomeUnknown("injected: response lost after the sell was sent")
            return res

        def submit_oco_once(symbol, qty, stop_price, take_profit_price, client_order_id):
            if inj._take("restore_reject"):
                inj._fire("restore_reject", client_order_id)
                raise OrderRejected("422 injected: protective order rejected", 422)
            return orig_oco(symbol, qty, stop_price, take_profit_price, client_order_id)

        def positions_snapshot():
            if inj._take("read_errors"):
                inj._fire("read_errors", "positions")
                from ..trading.alpaca_client import BrokerSnapshot
                return BrokerSnapshot(False, None, "injected read error")
            return orig_pos()

        for name, orig, fn in (("replace_order_once", orig_replace, replace_order_once),
                               ("submit_order_once", orig_submit, submit_order_once),
                               ("submit_oco_once", orig_oco, submit_oco_once),
                               ("positions_snapshot", orig_pos, positions_snapshot)):
            if orig is not None:
                setattr(broker, name, fn)
        return broker

    def on_transition(self, state: str) -> None:
        """Called by the exit manager after every transition."""
        if state in self.kill_states:
            self.kill_states.discard(state)
            self._fire("kill_at", state)
            self.kill(17)


class _PgProxy:
    """Stands in for the psycopg2 module: connect() fails inside the window."""

    def __init__(self, real, injector, clock):
        self._real, self._inj, self._clock = real, injector, clock

    def connect(self, *a, **k):
        if self._inj.db_down(self._clock()):
            if not self._inj.__dict__.get("_db_announced"):
                self._inj._db_announced = True
                self._inj._fire("db_outage", "window open: DB connections refused")
            raise self._real.OperationalError("injected DB outage (STAGING_FAULTS db_outage)")
        return self._real.connect(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _db_down(self, now: float) -> bool:
    if self.db_window is None:
        return False
    t = now - self.started
    return self.db_window[0] <= t < self.db_window[1]


def _wrap_db(self, recorder=None, lease_store=None, clock=None):
    """Route the recorder's and the lease store's connections through the
    outage window. Logs once when the window opens and closes."""
    import time as _t

    import psycopg2 as _pg

    clock = clock or _t.time
    self.started = getattr(self, "started", None) or clock()
    proxy = _PgProxy(_pg, self, clock)
    if recorder is not None and getattr(recorder, "_psycopg2", None) is not None:
        recorder._psycopg2 = proxy
    if lease_store is not None and hasattr(lease_store, "_pg") and not self.db_recorder_only:
        lease_store._pg = proxy
    a, b = self.db_window
    logger.warning("STAGING_FAULTS db_outage: DB connections will fail from +%.0f to +%.0f min", a / 60, b / 60)
    return proxy


FaultInjector.db_down = _db_down
FaultInjector.wrap_db = _wrap_db
FaultInjector.started = None


def from_env(env: Optional[Dict[str, str]] = None) -> Optional[FaultInjector]:
    env = os.environ if env is None else env
    spec = (env.get("STAGING_FAULTS") or "").strip()
    return FaultInjector(spec, env=env) if spec else None
