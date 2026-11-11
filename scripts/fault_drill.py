"""Stage 6c failure-injection drill - STAGING PAPER ACCOUNT ONLY.

Runs the REAL exit state machine (bot/reliability/exit_manager.py) and the
REAL Alpaca client against the staging paper account, with one injected
fault per scenario, on ONE share of a symbol outside the bot's universe
(default VZ). Every result is judged from Alpaca's own orders, fills and
position, never from log lines.

    python scripts/fault_drill.py [scenario ...]      (default: all live ones)

Scenarios (design test numbers)
  1  normal            reprice the take-profit; one sell fill; stop live until then
  3  replace_reject    two injected replace rejections -> fallback cancel -> one X sell
  6A drop_response     the replace is sent but its response is "lost" -> adopted, not resent
  6B sell_lost         fallback sell "lost" before sending -> resent with the SAME id only
 11  duplicate         the same exit requested twice and again by a fresh manager
 12  restart           manager discarded mid-exit; a fresh one rebuilds from Alpaca
 14  restore_reject    fallback sell rejected, protection restore rejected once -> alerts

Safety (same guard as the 6a spike)
  * BOT_ENVIRONMENT=staging, paper endpoint, no live keys, and Alpaca must
    confirm EXPECTED_ALPACA_ACCOUNT_NUMBER - otherwise it refuses.
  * Market hours only. Run it while the staging bot is NOT running (one-off
    start command), so the two never act on the account at the same time.
  * Before each scenario and at the end, every working order on the drill
    symbol is cancelled (taken from the full history) and the share is sold.

Output: one 'DRILL_RESULT {json}' line per scenario plus invariant checks.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location("alpaca_verify_spike",
                                     os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "alpaca_verify_spike.py"))
_spike = _ilu.module_from_spec(_spec)
sys.modules[_spec.name] = _spike
_spec.loader.exec_module(_spike)
DATA, PAPER, Api, guard = _spike.DATA, _spike.PAPER, _spike.Api, _spike.guard
last_price, position_qty, wait = _spike.last_price, _spike.position_qty, _spike.wait

SYMBOL = os.environ.get("DRILL_SYMBOL", "VZ")
RUN = uuid.uuid4().hex[:6]
LIVE = ["normal", "replace_reject", "drop_response", "sell_lost", "duplicate", "restart", "restore_reject"]
WORKING = {"new", "held", "accepted", "pending_new", "partially_filled", "pending_replace", "pending_cancel"}


def out(kind, **kw):
    row = {"kind": kind, "run": RUN, "at": datetime.now(timezone.utc).isoformat(), **kw}
    print("DRILL_RESULT " + json.dumps(row, default=str), flush=True)
    return row


class Alerts:
    def __init__(self):
        self.raised = []

    def raise_(self, key, text, severity="warning", repeat_s=None):
        self.raised.append((key, severity))
        print(f"DRILL_ALERT {severity} {key}: {text}", flush=True)
        return True

    def resolve(self, key, text=None, severity="warning"):
        return True


def history(api):
    return api.call("GET", "/v2/orders", params={"status": "all", "symbols": SYMBOL, "limit": 100,
                                                 "nested": "false"})[1] or []


def invariant_check(api, bought, seen):
    pos = position_qty(api, SYMBOL)
    hist = history(api)
    sold = sum(float(o.get("filled_qty") or 0) for o in hist
               if o.get("side") == "sell" and o.get("filled_at") and o.get("created_at", "") >= seen["since"])
    live = [o for o in hist if o.get("side") == "sell" and o.get("type") != "stop"
            and o.get("status") in WORKING and o.get("created_at", "") >= seen["since"]]
    # An order being replaced and its replacement are ONE sell (live drill
    # 11 Nov: a read mid-replace saw both and reported "2 working sells").
    replacing = {o.get("replaces") for o in live if o.get("replaces")}
    live = [o for o in live if o.get("id") not in replacing]
    bad = []
    if pos < 0:
        bad.append("short position")
    if sold > bought + 1e-6:
        bad.append(f"sold {sold} > bought {bought}")
    if len(live) > 1:
        bad.append(f"{len(live)} working sells")
    seen["max_live"] = max(seen.get("max_live", 0), len(live))
    if bad:
        seen.setdefault("breaches", []).extend(bad)
    return pos


def flatten(api):
    for o in history(api):
        if o.get("status") in WORKING and o.get("side") == "sell":
            api.call("DELETE", f"/v2/orders/{o['id']}")
    wait(lambda: not [o for o in history(api) if o.get("status") in WORKING and o.get("side") == "sell"], 20)
    if position_qty(api, SYMBOL) > 0:
        api.call("DELETE", f"/v2/positions/{SYMBOL}")
        wait(lambda: position_qty(api, SYMBOL) == 0, 30)
    left = position_qty(api, SYMBOL)
    if left > 0:
        out("flatten_incomplete", position=left,
            incident="CRITICAL: drill symbol still held after cleanup; check for a working stop")


def open_bracket(api, data, tag):
    bid, last = last_price(data, SYMBOL)
    px = last or bid
    body = {"symbol": SYMBOL, "qty": "1", "side": "buy", "type": "market", "time_in_force": "gtc",
            "order_class": "bracket", "client_order_id": f"drill-{RUN}-{tag}",
            "take_profit": {"limit_price": str(round(px * 1.10, 2))},
            "stop_loss": {"stop_price": str(round(px * 0.90, 2))}}
    code, o = api.call("POST", "/v2/orders", body)
    if code != 200:
        raise RuntimeError(f"bracket buy refused: {code} {o}")
    if not wait(lambda: position_qty(api, SYMBOL) >= 1, 60):
        raise RuntimeError("bracket buy did not fill in 60 s")
    time.sleep(2)
    return o


def make_manager(broker, faults_spec, alerts, protection=None):
    from bot.reliability.exit_manager import ExitManager
    from bot.reliability.fault_injection import FaultInjector

    m = ExitManager(broker, alerts, "s", protection=protection, lease_ok=lambda: True)
    if faults_spec:
        f = FaultInjector(faults_spec)            # refuses unless BOT_ENVIRONMENT=staging
        f.wrap(broker)
        m.faults = f
    return m


def drive(m, api, bought, seen, minutes=6.0, every=3.0):
    end = time.time() + minutes * 60
    while time.time() < end and SYMBOL in m.exits:
        m.step(SYMBOL)
        invariant_check(api, bought, seen)
        time.sleep(every)
    return m.exits.get(SYMBOL)


def scenario(name, api, data, broker_factory):
    flatten(api)
    seen = {"since": datetime.now(timezone.utc).isoformat()}
    alerts = Alerts()
    open_bracket(api, data, name[:8])
    spec = {"replace_reject": "replace_reject:2", "drop_response": "replace_drop_response",
            "sell_lost": "replace_reject:2,sell_lost", "restore_reject": "replace_reject:2,sell_reject,restore_reject"
            }.get(name, "")
    broker = broker_factory()
    prot = None
    if name == "restore_reject":
        prot = _protection(broker, alerts)
    m = make_manager(broker, spec, alerts, prot)
    m.request(SYMBOL, f"6c drill {name}")
    if name == "duplicate":
        m.request(SYMBOL, f"6c drill {name} (second reason)")
        m2 = make_manager(broker_factory(), "", alerts)
        m2.request(SYMBOL, f"6c drill {name} (after restart)")
    if name == "restart":
        time.sleep(3)
        m = make_manager(broker_factory(), "", alerts)       # the old manager is gone
        m.request(SYMBOL, f"6c drill {name} (rebuilt)")
    final = drive(m, api, 1.0, seen)
    pos = invariant_check(api, 1.0, seen)
    hist = history(api)
    ours = [{k: o.get(k) for k in ("client_order_id", "type", "status", "filled_qty", "filled_avg_price",
                                    "limit_price", "replaces", "replaced_by")}
            for o in hist if o.get("created_at", "") >= seen["since"]]
    row = out("scenario", scenario=name, faults=spec, final_state=final.state if final else "RECONCILED",
              position_after=pos, max_working_sells=seen.get("max_live", 0),
              breaches=seen.get("breaches", []), fired=getattr(getattr(m, "faults", None), "fired", []),
              alerts=alerts.raised, orders=ours,
              passed=not seen.get("breaches") and (pos == 0 or name == "restore_reject"))
    flatten(api)
    return row


def _protection(broker, alerts):
    from bot.config import load_config
    from bot.reliability.protection import ProtectionManager

    risk = load_config("nonexistent.ini").risk          # the staging bot's own risk settings
    return ProtectionManager(broker, risk, "s", alerts, dry_run=False)


def main():
    env = os.environ
    api = Api(env["ALPACA_API_KEY"], env["ALPACA_SECRET_KEY"], PAPER)
    data = Api(env["ALPACA_API_KEY"], env["ALPACA_SECRET_KEY"], DATA)
    account = guard(env, api)
    code, clock = api.call("GET", "/v2/clock")
    out("start", account=account, symbol=SYMBOL, market_open=(clock or {}).get("is_open"))
    if not (clock or {}).get("is_open"):
        out("refused", reason="market closed: the drill only runs in regular market hours")
        return 0

    from bot.trading.alpaca_client import AlpacaBroker

    def broker_factory():
        b = AlpacaBroker(env["ALPACA_API_KEY"], env["ALPACA_SECRET_KEY"], mode="paper", allow_submit=True)
        b.env_tag = "s"
        return b

    wanted = sys.argv[1:] or LIVE
    results = []
    for name in wanted:
        if name not in LIVE:
            out("skipped", scenario=name, reason="unknown scenario")
            continue
        try:
            results.append(scenario(name, api, data, broker_factory))
        except Exception as exc:  # noqa: BLE001
            results.append(out("scenario", scenario=name, error=repr(exc), passed=False))
            flatten(api)
    flatten(api)
    out("done", passed=sum(1 for r in results if r.get("passed")), total=len(results),
        position_after=position_qty(api, SYMBOL))
    return 0


if __name__ == "__main__":
    sys.exit(main())
