"""DQ3 correction (staging only): document a buy row whose recorded price is
not Alpaca's fill, with an insert-only audit trail.

Nothing existing is changed. For each given buy order it ADDS rows to
`ledger_corrections` (the Stage 3 table) that record, side by side:
  * the original recorded value (trades.price, and closed_trades.pnl of the
    round trip that buy opened),
  * Alpaca's actual fill (filled_avg_price, filled_qty, filled_at),
  * the corrected value, the reason, the time and the evidence.
Each row has a deterministic correction_key, so running it again adds
nothing. `trades` and `closed_trades` are checksummed before and after inside
the same transaction; any difference rolls everything back.

Default mode prints the plan and writes nothing. `--apply` writes.
Refuses unless BOT_ENVIRONMENT=staging and the Alpaca account is the expected
paper account. GET requests only to Alpaca.

    python scripts/ledger_entry_fill_correction.py --order-id=<alpaca buy order id> [--apply]
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = "dq3_entry_fill_correction"
REASON = ("DQ3: the buy row recorded the decision-time price, not Alpaca's filled_avg_price; "
          "fixed in code from commit (DQ3 fix) onward. Existing rows are not changed.")


def _args(name):
    return [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")]


def plan_for(order: dict, trade: dict, closed: dict | None, now_iso: str, evidence: str) -> list:
    """Pure: the ledger_corrections rows for one buy order (empty if it matches)."""
    oid = order["id"]
    fill = float(order["filled_avg_price"])
    fqty = float(order["filled_qty"])
    rec = float(trade["price"])
    if abs(rec - fill) < 1e-9:
        return []
    base = {"symbol": trade["symbol"], "source": SOURCE, "broker_order_ids": oid,
            "closed_trade_id": closed["id"] if closed else None}
    common = {"reason": REASON, "corrected_at": now_iso, "evidence": evidence, "trades_id": trade["id"],
              "order_id": oid, "client_order_id": order.get("client_order_id"),
              "recorded_price": rec, "requested_price": trade.get("requested_price"),
              "alpaca_filled_avg_price": fill, "alpaca_filled_qty": fqty,
              "alpaca_filled_at": order.get("filled_at")}
    rows = [{**base, "correction_key": f"entry_fill:{oid}:trades.price", "kind": "entry_fill_price",
             "field": "trades.price", "ledger_value": f"{rec:g}", "broker_value": f"{fill:g}",
             "detail": json.dumps({**common, "corrected_value": fill}, default=str)}]
    if closed is not None:
        qty, exit_p, pnl = float(closed["qty"]), float(closed["exit_price"]), float(closed["pnl"])
        true_pnl = round((exit_p - fill) * qty, 6)
        rows.append({**base, "correction_key": f"entry_fill:{oid}:closed_trades.pnl", "kind": "entry_fill_pnl",
                     "field": "closed_trades.pnl", "ledger_value": f"{round(pnl, 6):g}",
                     "broker_value": f"{true_pnl:g}",
                     "detail": json.dumps({**common, "closed_trades_id": closed["id"],
                                           "ledger_entry_price": float(closed["entry_price"]),
                                           "exit_price": exit_p, "qty": qty, "recorded_pnl": pnl,
                                           "corrected_entry_price": fill, "corrected_value": true_pnl},
                                          default=str)})
    return rows


def _checksum(cur, table):
    cur.execute(f"SELECT count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY id), '')) FROM {table} t")
    return cur.fetchone()


def main() -> int:
    env = os.environ
    if (env.get("BOT_ENVIRONMENT") or "").lower() != "staging":
        print("ENTRY_FILL_CORRECTION refused: staging only", flush=True)
        return 2
    order_ids = _args("order-id")
    if not order_ids:
        print("ENTRY_FILL_CORRECTION refused: give --order-id=<alpaca buy order id>", flush=True)
        return 2
    apply = "--apply" in sys.argv[1:]
    spec = importlib.util.spec_from_file_location("alpaca_verify_spike", os.path.join(HERE, "alpaca_verify_spike.py"))
    sp = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = sp
    spec.loader.exec_module(sp)
    api = sp.Api(env["ALPACA_API_KEY"], env["ALPACA_SECRET_KEY"], sp.PAPER)
    masked = sp.guard(env, api)                       # refuses unless the expected paper account
    import psycopg2
    import psycopg2.extras

    now_iso = datetime.now(timezone.utc).isoformat()
    out = {"account": masked, "mode": "apply" if apply else "plan", "orders": [], "rows": []}
    conn = psycopg2.connect(env["DATABASE_URL"], connect_timeout=10)
    try:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                before = {t: _checksum(cur, t) for t in ("trades", "closed_trades")}
                for oid in order_ids:
                    _, order = api.call("GET", f"/v2/orders/{oid}")
                    cur.execute("SELECT * FROM trades WHERE order_id=%s AND action='buy' ORDER BY id", (oid,))
                    trades = cur.fetchall()
                    if len(trades) != 1 or (order or {}).get("status") != "filled":
                        out["orders"].append({"order_id": oid, "skipped": f"{len(trades)} trades rows, "
                                              f"broker status {(order or {}).get('status')}"})
                        continue
                    trade = trades[0]
                    cur.execute("SELECT * FROM closed_trades WHERE symbol=%s AND ts >= %s AND entry_price=%s "
                                "ORDER BY ts LIMIT 1", (trade["symbol"], trade["ts"], trade["price"]))
                    closed = cur.fetchone()
                    evidence = (f"Alpaca GET /v2/orders/{oid} at {now_iso}; trades.id {trade['id']}"
                                + (f"; closed_trades.id {closed['id']}" if closed else ""))
                    rows = plan_for(order, trade, closed, now_iso, evidence)
                    out["orders"].append({"order_id": oid, "symbol": trade["symbol"], "recorded": str(trade["price"]),
                                          "filled_avg_price": order["filled_avg_price"],
                                          "closed_trades_id": closed["id"] if closed else None,
                                          "corrections": len(rows)})
                    out["rows"].extend(rows)
                    if apply:
                        for r in rows:
                            cur.execute(
                                "INSERT INTO ledger_corrections (correction_key, closed_trade_id, symbol, kind, field, "
                                "ledger_value, broker_value, detail, broker_order_ids, source) VALUES "
                                "(%(correction_key)s, %(closed_trade_id)s, %(symbol)s, %(kind)s, %(field)s, "
                                "%(ledger_value)s, %(broker_value)s, %(detail)s, %(broker_order_ids)s, %(source)s) "
                                "ON CONFLICT (correction_key) DO NOTHING", r)
                after = {t: _checksum(cur, t) for t in ("trades", "closed_trades")}
                out["existing_rows_unchanged"] = before == after
                out["checksums"] = {t: [before[t]["count"], hashlib.sha1(str(before[t]).encode()).hexdigest()[:12]]
                                    for t in before}
                if before != after:
                    raise RuntimeError("existing trades/closed_trades rows changed - rolled back")
                if not apply:
                    conn.rollback()
    finally:
        conn.close()
    print("ENTRY_FILL_CORRECTION " + json.dumps(out, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
