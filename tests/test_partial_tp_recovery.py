"""Review 13 Nov item 1: partial take-profit recovery (simulated).

The fake follows what the staging paper account showed on 12 Nov: a 20-share
OCO take-profit partly filled (19 of 20) and, ~0.2 s later, the linked stop
was no longer working while 1 share was still held."""
from types import SimpleNamespace

import pytest

from bot.reliability import exit_manager as em
from bot.reliability.protection import PROTECTED, UNKNOWN, ProtectionManager
from tests.test_exit_manager import Alerts, Clock
from tests.test_failure_injection import SharedBroker
from tests.test_protection import RISK


def scenario(filled=19.0):
    b = SharedBroker(qty=20.0)
    b.orders = [o for o in b.orders if o["side"] == "buy"]          # entry only
    g = "oco-normal"
    b.tp = b._add(side="sell", type="limit", status="partially_filled", qty=20.0, role="take_profit",
                  group=g, limit=55.0, cid="sP-AAPL-abcdef123456-1", oclass="oco", filled_qty=filled)
    b.stop = b._add(side="sell", type="stop", status="canceled", qty=20.0, role="stop_loss",
                    group=g, stop=41.0, oclass="oco")
    b.qty = 20.0 - filled
    b.held_for_orders = b.qty                                        # the TP remainder holds them
    c, al = Clock(), Alerts()
    pm = ProtectionManager(b, RISK, "s", al, clock=c)
    return b, c, al, pm


def run(pm, c, n=6, dt=10):
    out = None
    for _ in range(n):
        c.t += dt
        out = pm.sweep()
    return out


def cancels(b):
    return [x for x in b.calls if x[0] == "cancel"]


def ocos(b):
    return [x for x in b.calls if x[0] == "oco"]


def test_partial_tp_then_cancel_confirm_and_exact_quantity_oco():
    b, c, al, pm = scenario()
    pm.sweep()
    assert cancels(b) == [("cancel", b.tp["id"])] and ocos(b) == []          # cancel first, no stop yet
    assert ("prot_partial_tp:AAPL", "critical") in [(k, s) for k, s, _ in al.raised]
    c.t += 10
    pm.sweep()                                                                 # TP now cancelled -> restore
    assert len(ocos(b)) == 1 and ocos(b)[0][2] == 1.0                          # exactly the 1 share held
    assert run(pm, c, n=4, dt=10)["AAPL"] == PROTECTED                         # both legs confirmed working
    stops = [o for o in b.orders if o["type"] == "stop" and o["status"] == "held"]
    assert len(stops) == 1 and stops[0]["qty"] == 1.0 and b.qty == 1.0


def test_tp_fills_completely_during_the_cancel_no_order():
    b, c, al, pm = scenario()
    real = b.cancel_order_once

    def cancel_after_fill(oid, what):
        b._fill(b.tp, 55.0)                       # remainder fills before the cancel lands
        b.calls.append(("cancel", oid))
        return False
    b.cancel_order_once = cancel_after_fill
    pm.sweep()
    run(pm, c)
    assert b.qty == 0 and ocos(b) == []


def test_more_partial_fills_during_the_cancel_use_the_fresh_quantity():
    b, c, al, pm = scenario(filled=15.0)                     # 5 held
    real = b.cancel_order_once

    def cancel_after_more(oid, what):
        b._fill(b.tp, 55.0, qty=3)                             # 3 more sell first -> 2 held
        b.held_for_orders = 0.0
        return real(oid, what)
    b.cancel_order_once = cancel_after_more
    pm.sweep()
    c.t += 10
    pm.sweep()
    assert b.qty == 2.0 and ocos(b) and ocos(b)[0][2] == 2.0   # never the original quantity


def test_ambiguous_cancel_retries_the_cancel_only_then_escalates():
    b, c, al, pm = scenario()

    def lost(oid, what):
        b.calls.append(("cancel", oid))
        raise TimeoutError("response lost")
    b.cancel_order_once = lost
    for _ in range(12):
        c.t += 10
        pm.sweep()
    assert ocos(b) == [] and not [x for x in b.calls if x[0] == "submit"]     # never a sell, never a stop
    assert 2 <= len(cancels(b)) <= 6                                           # cancel retried at 20 s steps
    assert ("prot_manual:AAPL", "critical") in [(k, s) for k, s, _ in al.raised]   # escalated to the operator


def test_broker_reads_fail_nothing_is_sent():
    b, c, al, pm = scenario()
    b.read_ok = False
    run(pm, c, n=3)
    assert b.calls == []


def test_restart_during_recovery_rebuilds_from_the_broker():
    b, c, al, pm = scenario()
    pm.sweep()                                                 # cancel sent, then the process dies
    pm2 = ProtectionManager(b, RISK, "s", al, clock=c)          # nothing remembered
    c.t += 10
    pm2.sweep()
    assert len(ocos(b)) == 1 and ocos(b)[0][2] == 1.0          # TP gone -> restore the remainder once
    run(pm2, c, n=3)
    assert len(ocos(b)) == 1


def test_exit_manager_owns_a_partial_fill_during_an_exit():
    b, c, al, pm = scenario()
    xm = em.ExitManager(b, al, "s", protection=pm, clock=c, market_open=lambda: True)
    pm.exit_owns = xm.owns_recovery
    xm.exits["AAPL"] = em.Exit("AAPL", "sentiment", c.t, state=em.EXIT_WORKING,
                               history=[{"at": c.t, "from": "", "to": em.EXIT_WORKING, "why": "t"}])
    run(pm, c, n=3)
    assert b.calls == []                                       # no competing cancel or stop
    c.t += 700                                                 # ownership is bounded
    pm.sweep()
    assert cancels(b)


def test_inactive_instance_sends_nothing():
    b, c, al, pm = scenario()
    pm.active = lambda: False
    for _ in range(3):
        c.t += 400
        pm.tick()
    assert b.calls == []
