"""DQ3 audit (read-only): recorded BUY prices vs Alpaca's actual buy fills.

Reads the CSVs written by scripts/ledger_export_readonly.py (trades.csv,
closed_trades.csv, fills.csv) and writes nothing except its report. No
database, no broker, no network.

For every live (non-dry-run) buy row in `trades` with an order_id, it
aggregates that order's fills (quantity-weighted average across partial
fills) and compares price and quantity. For every closed_trades row it finds
the bot buy that opened it (same symbol, latest buy at or before the row's
entry_time, or before its exit time when entry_time is blank) and works out the realised-P/L effect of the entry-price error:

    recorded P/L - true P/L = (fill_price - recorded_entry_price) * qty

so a negative number means the ledger understated P/L.

Usage:
    python3 scripts/entry_fill_audit.py --data ledger_export [--out report.md]
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta


def _ts(s: str):
    s = (s or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    if "+" not in s[10:] and "-" not in s[10:]:
        s += "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.fromisoformat(s.split(".")[0] + s[-6:])


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load(data_dir, name):
    with open(os.path.join(data_dir, name), newline="") as fh:
        return list(csv.DictReader(fh))


def order_fills(fills):
    """order_id -> {qty, vwap, first_ts, last_ts, n} for BUY fills."""
    agg = defaultdict(lambda: {"qty": 0.0, "cost": 0.0, "n": 0, "first": None, "last": None})
    for r in fills:
        if (r.get("side") or "").lower() != "buy":
            continue
        q, p, t = _f(r.get("qty")) or 0.0, _f(r.get("price")) or 0.0, _ts(r.get("transaction_time"))
        a = agg[r.get("order_id")]
        a["qty"] += q
        a["cost"] += q * p
        a["n"] += 1
        a["first"] = t if a["first"] is None or (t and t < a["first"]) else a["first"]
        a["last"] = t if a["last"] is None or (t and t > a["last"]) else a["last"]
    return {k: {**v, "vwap": v["cost"] / v["qty"] if v["qty"] else None} for k, v in agg.items()}


def audit(data_dir):
    trades, closed, fills = load(data_dir, "trades.csv"), load(data_dir, "closed_trades.csv"), load(data_dir, "fills.csv")
    by_order = order_fills(fills)
    buys = [r for r in trades if r.get("action") == "buy"
            and (r.get("dry_run") or "").lower() not in ("true", "t", "1")]
    rows = []
    for r in buys:
        oid = r.get("order_id") or ""
        f = by_order.get(oid)
        rec_p, rec_q = _f(r.get("price")), _f(r.get("qty"))
        row = {"trade_id": r.get("id"), "ts": r.get("ts"), "symbol": r.get("symbol"),
               "path": "adopted" if (r.get("reason") or "").startswith("adopted") else "strategy",
               "order_id": oid, "recorded_price": rec_p, "recorded_qty": rec_q,
               "fill_price": None, "fill_qty": None, "n_fills": 0, "price_diff": None,
               "qty_diff": None, "cost_diff": None, "status": "no_fills_for_order_id"}
        if f and f["qty"]:
            row.update(fill_price=round(f["vwap"], 6), fill_qty=f["qty"], n_fills=f["n"],
                       price_diff=round(rec_p - f["vwap"], 6) if rec_p is not None else None,
                       qty_diff=(rec_q - f["qty"]) if rec_q is not None else None,
                       cost_diff=round((rec_p - f["vwap"]) * f["qty"], 4) if rec_p is not None else None,
                       status="matched")
        rows.append(row)

    # closed trades -> opening buy (same symbol, latest buy at/before entry_time, within 10 min)
    buys_by_sym = defaultdict(list)
    for row in rows:
        buys_by_sym[row["symbol"]].append((_ts(row["ts"]), row))
    closed_rows = []
    for c in closed:
        sym = c.get("symbol")
        # entry_time is blank on older rows: fall back to the latest buy before the exit.
        et = _ts(c.get("entry_time")) or _ts(c.get("ts"))
        cand = [(t, b) for t, b in buys_by_sym.get(sym, []) if t and et and t <= et + timedelta(minutes=10)]
        b = max(cand, key=lambda x: x[0])[1] if cand else None
        qty, ep = _f(c.get("qty")) or 0.0, _f(c.get("entry_price"))
        item = {"closed_id": c.get("id"), "symbol": sym, "exit_ts": c.get("ts"), "qty": qty,
                "ledger_entry": ep, "fill_entry": None, "pnl_recorded": _f(c.get("pnl")),
                "pnl_effect": None, "buy_trade_id": b["trade_id"] if b else None,
                "note": c.get("buy_reason", "")[:24]}
        if b and b["fill_price"] is not None and ep is not None:
            item["fill_entry"] = b["fill_price"]
            # recorded - true = (exit-ep)q - (exit-fill)q = (fill-ep)q
            item["pnl_effect"] = round((b["fill_price"] - ep) * qty, 4)
        closed_rows.append(item)
    return rows, closed_rows


def report(rows, closed_rows) -> str:
    m = [r for r in rows if r["status"] == "matched"]
    strat = [r for r in m if r["path"] == "strategy"]
    diff = [r for r in strat if abs(r["price_diff"] or 0) >= 0.005]
    qdiff = [r for r in m if abs(r["qty_diff"] or 0) > 1e-9]
    eff = [c for c in closed_rows if c["pnl_effect"] is not None]
    L = ["# DQ3 audit: recorded buy prices vs Alpaca fills (read-only)", "",
         f"- Live buy rows: {len(rows)} ({len(strat)} strategy, {len(m) - len(strat)} adopted with fills); "
         f"no fills found for order_id: {len(rows) - len(m)}",
         f"- Strategy buys whose recorded price differs from the fill VWAP by >= $0.005: {len(diff)} of {len(strat)}",
         f"- Buys whose recorded qty differs from filled qty: {len(qdiff)}",
         f"- Sum over strategy buys of (recorded - fill) x filled qty: "
         f"${sum(r['cost_diff'] for r in strat):,.2f} (positive = cost overstated)",
         f"- Mean |price diff| per strategy buy: "
         f"${(sum(abs(r['price_diff']) for r in strat) / len(strat)) if strat else 0:,.4f}; "
         f"largest: {max(strat, key=lambda r: abs(r['price_diff']))['symbol'] if strat else '-'} "
         f"${max((abs(r['price_diff']) for r in strat), default=0):,.4f}",
         f"- Closed trades linked to a filled buy: {len(eff)} of {len(closed_rows)}; "
         f"total realised-P/L effect (recorded - true): ${sum(c['pnl_effect'] for c in eff):,.2f}",
         f"- Partial-fill orders (more than one fill): {sum(1 for r in m if r['n_fills'] > 1)}", "",
         "## Buy rows", "",
         "| trade id | ts | symbol | path | recorded qty @ price | filled qty @ VWAP (fills) | price diff | cost diff |",
         "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['trade_id']} | {str(r['ts'])[:16]} | {r['symbol']} | {r['path']} | "
                 f"{r['recorded_qty']:g} @ {r['recorded_price']:.4f} | "
                 + (f"{r['fill_qty']:g} @ {r['fill_price']:.4f} ({r['n_fills']}) | {r['price_diff']:+.4f} | {r['cost_diff']:+.2f} |"
                    if r["status"] == "matched" else "no fills for this order_id | | |"))
    L += ["", "## Closed trades: realised-P/L effect of the entry price", "",
          "| closed id | symbol | qty | ledger entry | fill entry | recorded P/L | effect (recorded - true) |",
          "|---|---|---|---|---|---|---|"]
    for c in closed_rows:
        L.append(f"| {c['closed_id']} | {c['symbol']} | {c['qty']:g} | {c['ledger_entry']} | "
                 f"{'' if c['fill_entry'] is None else round(c['fill_entry'], 4)} | {c['pnl_recorded']} | "
                 f"{'' if c['pnl_effect'] is None else c['pnl_effect']} |")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    rows, closed_rows = audit(a.data)
    text = report(rows, closed_rows)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(text)
    print(text.split("## Buy rows")[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
