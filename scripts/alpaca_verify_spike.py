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
  * --oco-partial (review 11 Nov): V4 replaces the take-profit of the exact
    OCO the protection manager places (1 share) and checks the stop; V5
    tries to produce a partial take-profit fill (5 shares, up to 3 tries)
    and records what Alpaca does to the linked stop's quantity. Market
    hours only.
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
    left = position_qty(api, SYMBOL)
    log("cleanup", symbol=SYMBOL, position=left,
        open_orders=len([o for o in open_orders() if o.get("symbol") == SYMBOL]))
    reconcile_after_cancel(api, left)


def reconcile_after_cancel(api, left):
    """Review 11 Nov (VZ incident): after any cancel that could affect
    protection, check the broker straight away - don't wait for a sweep. A
    share left with no working stop is reported as a critical incident."""
    if left <= 0:
        return
    hist = api.call("GET", "/v2/orders", params={"status": "all", "symbols": SYMBOL, "limit": 100,
                                                 "nested": "false"})[1] or []
    stops = [o for o in hist if o.get("side") == "sell" and o.get("type") in ("stop", "stop_limit")
             and o.get("status") in ("new", "held", "accepted", "pending_new", "partially_filled")]
    log("cleanup.reconcile", symbol=SYMBOL, position=left, working_stops=len(stops),
        incident=None if stops else "CRITICAL: position left WITHOUT a working stop")


# ---- V4 / V5 (review 11 Nov): OCO replacement and partial take-profit fills ---------
def _oco_body(qty, tp, stop, tag):
    # Exactly the body AlpacaBroker.submit_oco_once sends for protection.
    return {"symbol": SYMBOL, "qty": str(qty), "side": "sell", "type": "limit", "time_in_force": "gtc",
            "order_class": "oco", "limit_price": str(round(tp, 2)),
            "take_profit": {"limit_price": str(round(tp, 2))},
            "stop_loss": {"stop_price": str(round(stop, 2))}, "client_order_id": cid(tag)}


def _buy(api, qty, tag):
    code, o = api.call("POST", "/v2/orders", {"symbol": SYMBOL, "qty": str(qty), "side": "buy",
                                              "type": "market", "time_in_force": "day",
                                              "client_order_id": cid(tag)})
    if code != 200 or not wait(lambda: position_qty(api, SYMBOL) >= qty, 60):
        raise RuntimeError(f"buy {qty} {SYMBOL} failed: {code} {o}")
    time.sleep(1)


def _oco_view(api, oid):
    o = order(api, oid)
    legs = o.get("legs") or []
    view = lambda x: {k: x.get(k) for k in ("id", "client_order_id", "type", "status", "qty",  # noqa: E731
                                           "filled_qty", "limit_price", "stop_price", "group_id",
                                           "group_role", "order_class", "replaces", "replaced_by")}
    return view(o), [view(l) for l in legs]


def _working_for_symbol(api):
    hist = api.call("GET", "/v2/orders", params={"status": "all", "symbols": SYMBOL, "limit": 100,
                                                 "nested": "false"})[1] or []
    return [{k: o.get(k) for k in ("id", "client_order_id", "type", "status", "qty", "filled_qty",
                                    "group_id", "group_role", "replaces")}
            for o in hist if o.get("status") in ("new", "held", "accepted", "pending_new",
                                                  "partially_filled", "pending_replace")]


def v4(api, data):
    """Replace the take-profit of the exact OCO the protection manager places."""
    bid, last = last_price(data, SYMBOL)
    _buy(api, 1, "v4-buy")
    code, o = api.call("POST", "/v2/orders", _oco_body(1, last * 1.10, last * 0.90, "v4-oco"))
    if code != 200:
        return log("V4.oco_submit", http=code, body=o, conclusion="OCO refused")
    time.sleep(2)
    parent, legs = _oco_view(api, o["id"])
    log("V4.oco_shape", parent=parent, legs=legs)
    c1, r1 = api.call("PATCH", f"/v2/orders/{o['id']}",
                      {"limit_price": str(round(last * 1.12, 2)), "client_order_id": cid("v4-r1")})
    time.sleep(2)
    old, _ = _oco_view(api, o["id"])
    new, new_legs = _oco_view(api, (r1 or {}).get("id") or "") if c1 == 200 else ({}, [])
    work = _working_for_symbol(api)
    stops = [w for w in work if w["type"] in ("stop", "stop_limit")]
    log("V4.replace_tp", http=c1, response_status=(r1 or {}).get("status"), old=old, new=new,
        new_legs=new_legs, working=work,
        stop_still_working=len(stops) == 1, stop_qty=stops[0]["qty"] if stops else None,
        stop_group_matches=bool(stops) and stops[0]["group_id"] == parent.get("group_id"),
        conclusion=("stop kept, same group, qty 1" if c1 == 200 and len(stops) == 1
                    and stops[0]["group_id"] == parent.get("group_id") and float(stops[0]["qty"]) == 1
                    else "CHECK: stop not as expected after OCO replace"))
    # Marketable replacement -> fill -> linked stop should be cancelled.
    if c1 == 200:
        bid2, _ = last_price(data, SYMBOL)
        c2, r2 = api.call("PATCH", f"/v2/orders/{r1['id']}",
                          {"limit_price": str(round((bid2 or last) * 0.995, 2)), "client_order_id": cid("v4-r2")})
        filled = wait(lambda: position_qty(api, SYMBOL) == 0, 30)
        log("V4.replace_marketable", http=c2, filled=bool(filled), working_after=_working_for_symbol(api),
            position_after=position_qty(api, SYMBOL))
        # Does Alpaca cancel the OCO stop on its own after the fill? (11 Nov:
        # still 'held' 0.4 s after; the clean-up then cancelled it.) Wait up
        # to 30 s WITHOUT cancelling anything and record what happens.
        gone = wait(lambda: not [w for w in _working_for_symbol(api) if w["type"] in ("stop", "stop_limit")], 30)
        log("V4.stop_after_fill", auto_cancelled_within_30s=bool(gone),
            working=_working_for_symbol(api),
            conclusion="Alpaca cancelled the OCO stop itself" if gone else
                       "STOP STILL WORKING 30 s after the take-profit filled (0 shares held)")


def v5(api, data, qty=5, tries=3):
    """Partial take-profit fill: what does Alpaca do to the linked stop's qty?
    Paper fills are simulated; partial fills can't be forced, so this tries a
    few times and reports 'not reproduced' if none happens."""
    for n in range(1, tries + 1):
        bid, last = last_price(data, SYMBOL)
        _buy(api, qty, f"v5-buy{n}")
        code, o = api.call("POST", "/v2/orders", _oco_body(qty, last * 1.10, last * 0.90, f"v5-oco{n}"))
        if code != 200:
            return log("V5.oco_submit", http=code, body=o)
        time.sleep(2)
        bid2, _ = last_price(data, SYMBOL)
        c, r = api.call("PATCH", f"/v2/orders/{o['id']}",
                        {"limit_price": str(round((bid2 or last) * 0.999, 2)), "client_order_id": cid(f"v5-r{n}")})
        partial = None
        end = time.time() + 20
        while time.time() < end:
            tp = order(api, (r or {}).get("id") or o["id"])
            fq = float(tp.get("filled_qty") or 0)
            if 0 < fq < qty:
                partial = tp
                break
            if fq >= qty:
                break
            time.sleep(0.1)
        if partial is not None:
            # Sample at once (11 Nov run: after a 2 s pause the partial had
            # already completed and the stop was gone, so nothing could be
            # concluded about the stop's quantity DURING the partial).
            work = _working_for_symbol(api)
            stops = [w for w in work if w["type"] in ("stop", "stop_limit")]
            held = position_qty(api, SYMBOL)
            if held <= 0 or not stops:
                log("V5.partial_fill", attempt=n, tp_filled=partial.get("filled_qty"), position=held,
                    stops=stops, conclusion="partial completed before the stop could be observed - "
                                            "NOT VERIFIED")
                cleanup(api)
                continue
            log("V5.partial_fill", attempt=n, tp_filled=partial.get("filled_qty"), position=held,
                stops=stops, stop_qty_vs_position=[(float(s_["qty"]) - float(s_["filled_qty"] or 0), held)
                                                   for s_ in stops],
                conclusion=("stop shrank to the remaining position" if stops and
                            abs(float(stops[0]["qty"]) - held) < 1e-6 else
                            "STOP DID NOT SHRINK (or is missing): over/under-protection after a partial fill"))
            return
        log("V5.attempt", attempt=n, http=c, result="filled in full (no partial)",
            position_after=position_qty(api, SYMBOL))
        cleanup(api)
    log("V5.partial_fill", conclusion=f"not reproduced in {tries} tries on paper (fills simulated)")


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
    if "--oco-partial" in sys.argv:
        _, clock = api.call("GET", "/v2/clock")
        if not (clock or {}).get("is_open"):
            log("V4V5.skipped", reason="market closed; V4/V5 need market hours")
        else:
            try:
                v4(api, data)
                cleanup(api)
                v5(api, data)
            finally:
                cleanup(api)
        print(f"SPIKE_DONE run={RUN} oco-partial", flush=True)
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
