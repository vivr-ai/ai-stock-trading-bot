"""Read-only staging account snapshot (pre-activation checks and evidence
between test entries; review 14 Nov items 2-3).

Prints one 'ACCOUNT_SNAPSHOT {json}' line with:
  * the Alpaca account identity (masked) - refuses unless it matches
    EXPECTED_ALPACA_ACCOUNT_NUMBER and BOT_ENVIRONMENT=staging;
  * every open position (symbol, qty, avg entry, market value);
  * every working order account-wide (open orders, nested legs flattened) -
    including held stop legs that the open list hides, by also reading the
    full history of each symbol seen in the last 48 h;
  * optionally (--symbol=VZ --since=ISO) the symbol's orders, fills and the
    bot's trades / closed_trades / journal rows since that time, so the
    ledger can be compared with Alpaca.
Sends nothing to Alpaca except GET requests.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORKING = {"new", "held", "accepted", "pending_new", "partially_filled", "pending_replace",
           "pending_cancel", "accepted_for_bidding"}


def _arg(name):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), None)


def main() -> int:
    env = os.environ
    if (env.get("BOT_ENVIRONMENT") or "").lower() != "staging":
        print("ACCOUNT_SNAPSHOT refused: staging only", flush=True)
        return 2
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "alpaca_verify_spike", os.path.join(os.path.dirname(os.path.abspath(__file__)), "alpaca_verify_spike.py"))
    sp = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = sp
    spec.loader.exec_module(sp)
    api = sp.Api(env["ALPACA_API_KEY"], env["ALPACA_SECRET_KEY"], sp.PAPER)
    masked = sp.guard(env, api)                       # refuses unless the expected paper account
    _, positions = api.call("GET", "/v2/positions")
    _, open_orders = api.call("GET", "/v2/orders", params={"status": "open", "nested": "true", "limit": 500})
    flat = []
    for o in open_orders or []:
        flat.append(o)
        flat.extend(o.get("legs") or [])
    from datetime import datetime, timedelta, timezone

    after = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    _, recent = api.call("GET", "/v2/orders", params={"status": "all", "after": after, "limit": 500,
                                                      "nested": "false"})
    seen = {o["id"] for o in flat}
    for sym in sorted({r.get("symbol") for r in recent or [] if r.get("symbol")}):
        _, hist = api.call("GET", "/v2/orders", params={"status": "all", "symbols": sym, "limit": 200,
                                                        "nested": "false"})
        for o in hist or []:
            if o.get("status") in WORKING and o["id"] not in seen:
                flat.append(o)
                seen.add(o["id"])
    working = [{k: o.get(k) for k in ("symbol", "client_order_id", "side", "type", "status", "qty",
                                      "filled_qty", "limit_price", "stop_price", "group_role", "order_class")}
               for o in flat if o.get("status") in WORKING]
    report = {"account": masked,
              "positions": [{k: p.get(k) for k in ("symbol", "qty", "avg_entry_price", "market_value")}
                            for p in positions or []],
              "working_orders": working, "n_positions": len(positions or []), "n_working": len(working)}
    sym, since = _arg("symbol"), _arg("since")
    if sym:
        _, hist = api.call("GET", "/v2/orders", params={"status": "all", "symbols": sym, "limit": 100,
                                                        "nested": "false", **({"after": since} if since else {})})
        report["symbol_orders"] = [{k: o.get(k) for k in ("client_order_id", "side", "type", "status", "qty",
                                                          "filled_qty", "filled_avg_price", "limit_price",
                                                          "stop_price", "group_role", "replaces",
                                                          "submitted_at", "filled_at")} for o in hist or []]
        try:
            import psycopg2

            with psycopg2.connect(env["DATABASE_URL"], connect_timeout=10) as c, c.cursor() as cur:
                cur.execute("SELECT ts, action, qty, price, order_id, status, entry_path, reason FROM trades "
                            "WHERE symbol=%s AND ts >= COALESCE(%s::timestamptz, '-infinity') ORDER BY ts",
                            (sym, since))
                report["db_trades"] = [list(map(str, r)) for r in cur.fetchall()]
                cur.execute("SELECT ts, qty, entry_price, exit_price, pnl, exit_reason FROM closed_trades "
                            "WHERE symbol=%s AND ts >= COALESCE(%s::timestamptz, '-infinity') ORDER BY ts",
                            (sym, since))
                report["db_closed_trades"] = [list(map(str, r)) for r in cur.fetchall()]
                cur.execute("SELECT count(*) FROM journal_events WHERE payload::text LIKE %s "
                            "AND journaled_at >= COALESCE(%s::timestamptz, '-infinity')", (f"%{sym}%", since))
                report["db_journal_events_mentioning_symbol"] = cur.fetchone()[0]
        except Exception as exc:  # noqa: BLE001
            report["db_error"] = str(exc)
    print("ACCOUNT_SNAPSHOT " + json.dumps(report, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
