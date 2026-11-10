"""Stage 6a verification spike (V1-V3) - STAGING PAPER ACCOUNT ONLY.

Establishes, from Alpaca's actual behaviour rather than documentation:
  V1  what happens to a duplicate client_order_id (identical resubmission,
      reuse after cancel, and an uncertain submission resolved by lookup)
  V2  whether paper orders carry group_id / group_role on bracket parents and
      legs, and whether the installed alpaca-py parses them
  V3  replacing a bracket's take-profit leg: acceptance, what happens to the
      stop leg, replaces/replaced_by and group_id on the new order,
      rejection, a second replace while one is pending, and the race where
      the original take-profit fills during the replace (best effort)

Safety
  * Refuses to run unless BOT_ENVIRONMENT=staging, the API base URL is the
    paper endpoint, no ALPACA_LIVE_* keys are present, and Alpaca confirms
    the keys belong to EXPECTED_ALPACA_ACCOUNT_NUMBER.
  * Trades ONE share of a symbol outside the bot's universe (default VZ).
    Every order carries a 'spike-' client_order_id. Cleanup cancels only
    this symbol's spike orders and flattens only this symbol.
  * V3 needs the market open; outside hours it runs V1 and V2 only.
  * Run it while the staging bot is NOT running (e.g. as a one-off start
    command), so the two never act on the account at the same time.

Output: one 'SPIKE_RESULT {json}' log line per check, and a summary JSON
written to SPIKE_OUT (default /data/spike or ./spike_results).
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PAPER = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets"
SYMBOL = os.environ.get("SPIKE_SYMBOL", "VZ")
RUN = uuid.uuid4().hex[:6]
RESULTS = []


def log(check, **kw):
    row = {"check": check, "at": datetime.now(timezone.utc).isoformat(), **kw}
    RESULTS.append(row)
    print("SPIKE_RESULT " + json.dumps(row, default=str), flush=True)


class Api:
    def __init__(self, key, secret, base=PAPER, session=None):
        import requests

        self.base = base
        self.s = session or requests.Session()
        self.h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

    def call(self, method, path, body=None, params=None, timeout=15):
        r = self.s.request(method, self.base + path, headers=self.h, json=body, params=params,
                           timeout=timeout)
        try:
            data = r.json() if r.text else None
        except ValueError:
            data = r.text
        return r.status_code, data


def guard(env, api) -> str:
    if env.get("BOT_ENVIRONMENT", "").lower() != "staging":
        raise SystemExit("REFUSED: BOT_ENVIRONMENT must be 'staging'.")
    if env.get("ALPACA_LIVE_API_KEY") or env.get("ALPACA_LIVE_SECRET_KEY"):
        raise SystemExit("REFUSED: live keys present.")
    if api.base != PAPER:
        raise SystemExit("REFUSED: not the paper endpoint.")
    expected = (env.get("EXPECTED_ALPACA_ACCOUNT_NUMBER") or "").strip()
    if not expected:
        raise SystemExit("REFUSED: EXPECTED_ALPACA_ACCOUNT_NUMBER not set.")
    code, acct = api.call("GET", "/v2/account")
    if code != 200 or str(acct.get("account_number")) != expected:
        raise SystemExit("REFUSED: could not verify the staging account identity.")
    return "*" * (len(expected) - 4) + expected[-4:]


def cid(tag):
    return f"spike-{RUN}-{tag}"


def last_price(api_data, symbol):
    code, q = api_data.call("GET", f"/v2/stocks/{symbol}/quotes/latest", params={"feed": "iex"})
    code2, t = api_data.call("GET", f"/v2/stocks/{symbol}/trades/latest", params={"feed": "iex"})
    bid = (q or {}).get("quote", {}).get("bp") if code == 200 else None
    last = (t or {}).get("trade", {}).get("p") if code2 == 200 else None
    return bid, last


def order(api, oid):
    return api.call("GET", f"/v2/orders/{oid}", params={"nested": "true"})[1] or {}


def legs_of(o):
    return {(leg.get("group_role") or leg.get("type")): leg for leg in (o.get("legs") or [])}


def position_qty(api, symbol):
    code, p = api.call("GET", f"/v2/positions/{symbol}")
    return float(p.get("qty", 0)) if code == 200 else 0.0


def wait(pred, timeout=60, every=0.5):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(every)
    return None


# ---- V1 ---------------------------------------------------------------------
def v1(api, price):
    limit = round(price * 0.5, 2)
    body = {"symbol": SYMBOL, "qty": "1", "side": "buy", "type": "limit", "time_in_force": "gtc",
            "limit_price": str(limit), "client_order_id": cid("v1a")}
    c1, o1 = api.call("POST", "/v2/orders", body)
    c2, o2 = api.call("POST", "/v2/orders", body)
    log("V1.identical_resubmission", first_status=c1, first_order_status=(o1 or {}).get("status"),
        second_status=c2, second_body=o2,
        conclusion=("duplicate REJECTED" if c2 >= 400 else "duplicate ACCEPTED (not idempotent!)"))
    if c1 == 200:
        api.call("DELETE", f"/v2/orders/{o1['id']}")
        wait(lambda: order(api, o1["id"]).get("status") == "canceled", 20)
        c3, o3 = api.call("POST", "/v2/orders", body)
        log("V1.reuse_after_cancel", status=c3, body=o3 if c3 >= 400 else {"status": o3.get("status")},
            conclusion="reuse after cancel REJECTED" if c3 >= 400 else "reuse after cancel ACCEPTED")
        if c3 == 200:
            api.call("DELETE", f"/v2/orders/{o3['id']}")
    # Uncertain submission: send, but give up reading the response almost at once.
    body2 = dict(body, client_order_id=cid("v1b"))
    outcome = "response received"
    try:
        api.call("POST", "/v2/orders", body2, timeout=(10, 0.001))
    except Exception as exc:  # noqa: BLE001
        outcome = f"client timed out: {type(exc).__name__}"
    found = None
    for delay in (1, 2, 4, 8):
        time.sleep(delay)
        code, o = api.call("GET", "/v2/orders:by_client_order_id", params={"client_order_id": body2["client_order_id"]})
        if code == 200:
            found = o
            break
    log("V1.uncertain_submission", client_outcome=outcome,
        lookup=("found: " + str(found.get("status"))) if found else "not found after 15 s",
        conclusion="lookup by client_order_id resolves the uncertainty" if found else "order did not exist")
    if found:
        api.call("DELETE", f"/v2/orders/{found['id']}")
    code, o = api.call("GET", "/v2/orders:by_client_order_id", params={"client_order_id": cid("never")})
    log("V1.lookup_unknown_id", status=code, conclusion="404 means 'not found'" if code == 404 else "unexpected")


# ---- V2 ---------------------------------------------------------------------
def v2(api, price):
    body = {"symbol": SYMBOL, "qty": "1", "side": "buy", "type": "limit", "time_in_force": "gtc",
            "limit_price": str(round(price * 0.5, 2)), "order_class": "bracket",
            "take_profit": {"limit_price": str(round(price * 0.6, 2))},
            "stop_loss": {"stop_price": str(round(price * 0.45, 2))}, "client_order_id": cid("v2")}
    code, o = api.call("POST", "/v2/orders", body)
    if code != 200:
        log("V2.bracket_submit", status=code, body=o)
        return
    full = order(api, o["id"])
    legs = full.get("legs") or []
    sdk = "unknown"
    try:
        from alpaca.trading.models import Order
        parsed = Order(**full)
        sdk = "parses group_id" if getattr(parsed, "group_id", None) else "does NOT expose group_id"
    except Exception as exc:  # noqa: BLE001
        sdk = f"model failed: {exc}"
    log("V2.group_fields", parent_group_id=full.get("group_id"), parent_group_role=full.get("group_role"),
        legs=[{"type": leg.get("type"), "status": leg.get("status"), "group_id": leg.get("group_id"),
               "group_role": leg.get("group_role")} for leg in legs],
        same_group=len({full.get("group_id")} | {leg.get("group_id") for leg in legs}) == 1,
        alpaca_py=sdk,
        conclusion=("group_id/group_role PRESENT" if full.get("group_id") else "group fields ABSENT"))
    api.call("DELETE", f"/v2/orders/{o['id']}")


# ---- V3 ---------------------------------------------------------------------
def open_bracket(api, price, tag, tp_mult=1.10):
    body = {"symbol": SYMBOL, "qty": "1", "side": "buy", "type": "market", "time_in_force": "gtc",
            "order_class": "bracket", "take_profit": {"limit_price": str(round(price * tp_mult, 2))},
            "stop_loss": {"stop_price": str(round(price * 0.90, 2))}, "client_order_id": cid(tag)}
    code, o = api.call("POST", "/v2/orders", body)
    if code != 200:
        return None, f"submit {code}: {o}"
    if not wait(lambda: order(api, o["id"]).get("status") == "filled", 60):
        return None, "entry did not fill in 60 s"
    full = order(api, o["id"])
    return full, ""


def sample_stop(api, parent_id, seconds=20):
    seen = []
    end = time.time() + seconds
    while time.time() < end:
        full = order(api, parent_id)
        st = [(leg.get("group_role") or leg.get("type"), leg.get("status")) for leg in full.get("legs") or []]
        seen.append(st)
        if position_qty(api, SYMBOL) == 0:
            break
        time.sleep(0.5)
    return seen


def v3(api, data):
    bid, last = last_price(data, SYMBOL)
    if not last:
        log("V3.skipped", reason="no price")
        return
    # a) acceptance: reprice TP to a marketable limit; the stop should stay held.
    full, err = open_bracket(api, last, "v3a")
    if not full:
        log("V3a.setup_failed", reason=err)
        return
    lg = legs_of(full)
    tp, sl = lg.get("take_profit") or lg.get("limit"), lg.get("stop_loss") or lg.get("stop")
    bid, last = last_price(data, SYMBOL)
    t0 = time.time()
    code, new = api.call("PATCH", f"/v2/orders/{tp['id']}",
                         {"limit_price": str(round((bid or last) * 0.995, 2)), "client_order_id": cid("v3a-r1")})
    resp_ms = int((time.time() - t0) * 1000)
    samples = sample_stop(api, full["id"], 30)
    old_tp, new_o = order(api, tp["id"]), (order(api, new["id"]) if code == 200 else {})
    log("V3a.replace_accept", http=code, response_status=(new or {}).get("status"), response_ms=resp_ms,
        before={"tp": tp.get("status"), "stop": sl.get("status"), "group_id": full.get("group_id")},
        old_tp_status=old_tp.get("status"), old_tp_replaced_by=old_tp.get("replaced_by"),
        new_status=new_o.get("status"), new_replaces=new_o.get("replaces"),
        new_group_id=new_o.get("group_id"), new_group_role=new_o.get("group_role"),
        stop_final=order(api, sl["id"]).get("status"),
        stop_states_seen=sorted({s for snap in samples for r, s in snap if r in ("stop_loss", "stop")}),
        position_after=position_qty(api, SYMBOL))
    cleanup(api)

    # b) rejection: an impossible quantity; the bracket must stay intact.
    bid, last = last_price(data, SYMBOL)
    full, err = open_bracket(api, last, "v3b")
    if full:
        lg = legs_of(full)
        tp, sl = lg.get("take_profit") or lg.get("limit"), lg.get("stop_loss") or lg.get("stop")
        code, body = api.call("PATCH", f"/v2/orders/{tp['id']}", {"qty": "999"})
        time.sleep(2)
        log("V3b.replace_rejected", http=code, body=body, tp_after=order(api, tp["id"]).get("status"),
            stop_after=order(api, sl["id"]).get("status"), position=position_qty(api, SYMBOL))
        # c) a second replace while the first is still pending
        code1, n1 = api.call("PATCH", f"/v2/orders/{tp['id']}", {"limit_price": str(round(last * 1.20, 2))})
        code2, n2 = api.call("PATCH", f"/v2/orders/{tp['id']}", {"limit_price": str(round(last * 1.25, 2))})
        time.sleep(3)
        log("V3c.double_replace", first=code1, second=code2, second_body=n2 if code2 >= 400 else None,
            stop_after=order(api, sl["id"]).get("status"))
        cleanup(api)
    else:
        log("V3b.setup_failed", reason=err)

    # d) race: original TP right at the market, replaced at once.
    bid, last = last_price(data, SYMBOL)
    full, err = open_bracket(api, last, "v3d", tp_mult=1.0005)
    if full:
        lg = legs_of(full)
        tp = lg.get("take_profit") or lg.get("limit")
        bid, last = last_price(data, SYMBOL)
        code, new = api.call("PATCH", f"/v2/orders/{tp['id']}",
                             {"limit_price": str(round((bid or last) * 0.995, 2)), "client_order_id": cid("v3d-r1")})
        time.sleep(10)
        code_a, acts = api.call("GET", "/v2/account/activities/FILL", params={"direction": "desc", "page_size": 20})
        def _t(v):
            return datetime.fromisoformat(str(v).replace("Z", "+00:00")) if v else None
        entry_t = _t(full.get("filled_at"))
        sells = [a for a in (acts or []) if a.get("symbol") == SYMBOL and a.get("side") == "sell"
                 and entry_t and _t(a.get("transaction_time")) and _t(a.get("transaction_time")) >= entry_t]
        log("V3d.race", replace_http=code, replace_status=(new or {}).get("status"),
            old_tp=order(api, tp["id"]).get("status"),
            new_order=(order(api, new["id"]).get("status") if code == 200 else None),
            sell_fills=len(sells), sell_qty=sum(float(a.get("qty", 0)) for a in sells),
            position_after=position_qty(api, SYMBOL),
            conclusion="no oversell" if position_qty(api, SYMBOL) >= 0 else "SHORT POSITION - oversell")
        cleanup(api)
    else:
        log("V3d.setup_failed", reason=err)


def cleanup(api):
    """Cancel EVERY open order on the spike symbol, then flatten it.

    The symbol is outside the bot's universe, so everything on it belongs to
    the spike. (First run, 9 Nov: cancelling only 'spike-' client IDs left a
    replaced take-profit leg - Alpaca gives replacements and bracket legs
    their own IDs - holding the share, so one VZ share was left behind.)"""
    def open_orders():
        return api.call("GET", "/v2/orders", params={"status": "open", "symbols": SYMBOL,
                                                      "nested": "true"})[1] or []

    # Record exactly how Alpaca lists this symbol's orders before touching
    # them (Stage 6b input: after a take-profit replacement the bot's
    # protection read did not see a stop for the leftover share).
    def brief(o):
        return {k: o.get(k) for k in ("id", "client_order_id", "type", "side", "status", "qty",
                                      "limit_price", "stop_price", "order_class", "group_id",
                                      "group_role", "replaces", "replaced_by")}
    recent = api.call("GET", "/v2/orders", params={"status": "all", "symbols": SYMBOL, "limit": 50,
                                                    "nested": "false"})[1] or []
    snap = open_orders()
    log("cleanup.orders_before", symbol=SYMBOL,
        open_top_level=[dict(brief(o), legs=[brief(l) for l in (o.get("legs") or [])]) for o in snap],
        recent_all=[brief(o) for o in recent[:20]])
    _, clock = api.call("GET", "/v2/clock")
    if position_qty(api, SYMBOL) != 0 and not (clock or {}).get("is_open"):
        # Cancelling now would strip the stop off a share that can't be sold
        # until the open. Leave everything in place.
        log("cleanup.skipped", reason="market closed: position and its orders left untouched; "
                                      "run --cleanup-only in market hours")
        return
    # Cancel every WORKING order on the symbol, taken from the full history:
    # the open list hides a held stop leg once its take-profit was replaced
    # (10 Nov). Every response is recorded.
    working = {"new", "held", "accepted", "pending_new", "partially_filled", "accepted_for_bidding"}
    targets = [o for o in recent if o.get("symbol") == SYMBOL and (o.get("status") or "") in working]
    for o in snap:
        if o.get("symbol") == SYMBOL and o["id"] not in {t["id"] for t in targets}:
            targets.append(o)
    for o in targets:
        code, body = api.call("DELETE", f"/v2/orders/{o['id']}")
        log("cleanup.cancel", order=o["id"], role=o.get("group_role"), status=o.get("status"),
            http=code, body=body)
    wait(lambda: not [o for o in open_orders() if o.get("symbol") == SYMBOL], 20)
    if position_qty(api, SYMBOL) != 0:
        code, body = api.call("DELETE", f"/v2/positions/{SYMBOL}")
        log("cleanup.flatten", http=code, body=body)
        wait(lambda: position_qty(api, SYMBOL) == 0, 30)
    log("cleanup", symbol=SYMBOL, position=position_qty(api, SYMBOL),
        open_orders=len([o for o in open_orders() if o.get("symbol") == SYMBOL]))


def main() -> int:
    env = os.environ
    key, secret = env.get("ALPACA_API_KEY"), env.get("ALPACA_SECRET_KEY")
    if not (key and secret):
        raise SystemExit("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
    api, data = Api(key, secret), Api(key, secret, base=DATA)
    data.base = DATA
    masked = guard(env, api)
    from bot.universe.static_universe import STATIC_UNIVERSE
    if SYMBOL in STATIC_UNIVERSE:
        raise SystemExit(f"REFUSED: {SYMBOL} is in the bot's universe; choose another SPIKE_SYMBOL.")
    log("start", account=masked, symbol=SYMBOL, run=RUN)
    if "--cleanup-only" in sys.argv:
        cleanup(api)
        print(f"SPIKE_DONE run={RUN} cleanup-only", flush=True)
        return 0
    _, clock = api.call("GET", "/v2/clock")
    bid, last = last_price(data, SYMBOL)
    if not last:
        raise SystemExit("No price for the spike symbol.")
    try:
        v1(api, last)
        v2(api, last)
        if (clock or {}).get("is_open"):
            v3(api, data)
        else:
            log("V3.skipped", reason="market closed; V3 needs market hours")
    finally:
        cleanup(api)
    out = env.get("SPIKE_OUT") or ("/data/spike" if os.path.isdir("/data") else "spike_results")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, f"spike-{RUN}.json"), "w") as fh:
        json.dump(RESULTS, fh, indent=2, default=str)
    print(f"SPIKE_DONE run={RUN} results={len(RESULTS)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
