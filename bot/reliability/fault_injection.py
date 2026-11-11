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
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Dict, Optional

from ..trading.alpaca_client import OrderOutcomeUnknown, OrderRejected

logger = logging.getLogger(__name__)

KNOWN = {"replace_reject", "replace_drop_response", "sell_reject", "sell_drop_response", "sell_lost",
         "restore_reject", "read_errors", "kill_at"}


class FaultInjector:
    def __init__(self, spec: str, *, allow: bool = False, env: Optional[Dict[str, str]] = None,
                 kill: Callable[[int], None] = os._exit, on_fire: Callable[[str, str], None] = None):
        env = os.environ if env is None else env
        if not allow and (env.get("BOT_ENVIRONMENT") or "").lower() != "staging":
            raise RuntimeError("STAGING_FAULTS is only allowed when BOT_ENVIRONMENT=staging")
        self.counts: Dict[str, int] = {}
        self.kill_states = set()
        for part in filter(None, (p.strip() for p in (spec or "").split(","))):
            name, _, arg = part.partition(":")
            if name not in KNOWN:
                raise ValueError(f"unknown fault {name!r}; known: {sorted(KNOWN)}")
            if name == "kill_at":
                self.kill_states.add(arg)
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
        orig_replace, orig_submit = broker.replace_order_once, broker.submit_order_once
        orig_oco = getattr(broker, "submit_oco_once", None)
        orig_pos, orig_hist = broker.positions_snapshot, broker.symbol_orders_snapshot

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

        broker.replace_order_once = replace_order_once
        broker.submit_order_once = submit_order_once
        if orig_oco is not None:
            broker.submit_oco_once = submit_oco_once
        broker.positions_snapshot = positions_snapshot
        broker.symbol_orders_snapshot = orig_hist
        return broker

    def on_transition(self, state: str) -> None:
        """Called by the exit manager after every transition."""
        if state in self.kill_states:
            self.kill_states.discard(state)
            self._fire("kill_at", state)
            self.kill(17)


def from_env(env: Optional[Dict[str, str]] = None) -> Optional[FaultInjector]:
    env = os.environ if env is None else env
    spec = (env.get("STAGING_FAULTS") or "").strip()
    return FaultInjector(spec, env=env) if spec else None
