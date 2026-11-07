"""Stage 3 - ledger reconciliation, DRY RUN ONLY.

Rebuilds round trips (buy -> sell) from Alpaca's own fill records and compares
them with the bot's `closed_trades` ledger. It produces a report and a CSV of
*proposed* new rows. It never connects to the database and never writes to
it - applying anything is a separate, later step that needs explicit approval.

Inputs are CSV exports (the read-only export in
research/sentiment_exit_audit/export_data.py produces all three):
  fills.csv          Alpaca FILL activities (id, transaction_time, symbol,
                     side, qty, price, order_id, type)
  closed_trades.csv  the bot's closed_trades table
  trades.csv         the bot's trades table (order intents)

Method:
  1. Group fills by order_id (partial fills -> one execution, volume-weighted
     price, first and last fill time).
  2. Per symbol, in time order, match sells against earlier buys first-in
     first-out. Each sell order becomes one round trip.
  3. Match each round trip to a closed_trades row: same symbol, same quantity,
     ledger row written between 5 minutes before and 36 hours after the sell
     filled; nearest in time wins, one-to-one.
  4. Report: matched (with price/time differences), round trips missing from
     the ledger (-> proposed rows, labelled as from Alpaca fills), ledger rows
     with no fill evidence, buys with no `trades` row, and positions still
     open at the end of the fill history.

Usage:
  python scripts/ledger_reconcile_dryrun.py --data DIR --out DIR
"""
from __future__ import annotations

import argparse
import csv
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

SOURCE_LABEL = "alpaca_fills_reconciliation"
MATCH_BEFORE = timedelta(minutes=5)
MATCH_AFTER = timedelta(hours=36)
QTY_EPS = 1e-6


def parse_ts(s: str) -> datetime:
    s = (s or "").strip().replace("Z", "+00:00")
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _f(x) -> Optional[float]:
    try:
        return float(x) if x not in (None, "") else None
    except ValueError:
        return None


@dataclass
class Execution:
    order_id: str
    symbol: str
    side: str
    qty: float
    notional: float
    first_ts: datetime
    last_ts: datetime
    fill_count: int

    @property
    def price(self) -> float:
        return self.notional / self.qty if self.qty else 0.0


@dataclass
class RoundTrip:
    symbol: str
    qty: float
    entry_price: float
    exit_price: float
    entry_time: Optional[datetime]
    exit_time: datetime
    sell_order_id: str
    buy_order_ids: List[str] = field(default_factory=list)
    unmatched_sell_qty: float = 0.0   # sold more than the fill history shows bought

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.qty

    @property
    def pnl_pct(self) -> float:
        return ((self.exit_price - self.entry_price) / self.entry_price * 100.0
                if self.entry_price else 0.0)


def group_fills(fills: List[dict]) -> List[Execution]:
    by_order: Dict[str, Execution] = {}
    for r in fills:
        if (r.get("type") or "fill") not in ("fill", "partial_fill"):
            continue
        qty, price = _f(r["qty"]), _f(r["price"])
        if not qty or price is None:
            continue
        ts = parse_ts(r["transaction_time"])
        oid = r["order_id"]
        e = by_order.get(oid)
        if e is None:
            by_order[oid] = Execution(oid, r["symbol"], r["side"].lower(), qty, qty * price,
                                      ts, ts, 1)
        else:
            e.qty += qty
            e.notional += qty * price
            e.first_ts, e.last_ts = min(e.first_ts, ts), max(e.last_ts, ts)
            e.fill_count += 1
    return sorted(by_order.values(), key=lambda e: (e.first_ts, e.order_id))


def build_round_trips(execs: List[Execution]):
    """FIFO-match sells to buys per symbol. Returns (round_trips, open_lots)
    where open_lots maps symbol -> remaining [qty, price, ts, order_id]."""
    lots: Dict[str, List[list]] = {}
    trips: List[RoundTrip] = []
    for e in execs:
        book = lots.setdefault(e.symbol, [])
        if e.side == "buy":
            book.append([e.qty, e.price, e.first_ts, e.order_id])
            continue
        need, cost, matched = e.qty, 0.0, 0.0
        entry_time, buy_ids = None, []
        while need > QTY_EPS and book:
            lot = book[0]
            take = min(need, lot[0])
            cost += take * lot[1]
            matched += take
            entry_time = lot[2] if entry_time is None else min(entry_time, lot[2])
            if lot[3] not in buy_ids:
                buy_ids.append(lot[3])
            lot[0] -= take
            need -= take
            if lot[0] <= QTY_EPS:
                book.pop(0)
        trips.append(RoundTrip(
            symbol=e.symbol, qty=e.qty,
            entry_price=(cost / matched) if matched else 0.0,
            exit_price=e.price, entry_time=entry_time, exit_time=e.last_ts,
            sell_order_id=e.order_id, buy_order_ids=buy_ids,
            unmatched_sell_qty=max(0.0, need),
        ))
    open_lots = {s: b for s, b in lots.items() if b}
    return trips, open_lots


def exit_hint(rt: RoundTrip) -> str:
    """A label only - fills don't say which order type closed the trade."""
    if not rt.entry_price:
        return "exit reason unknown"
    ratio = rt.exit_price / rt.entry_price
    if abs(ratio - 0.90) <= 0.006:
        return "price consistent with the -10% bracket stop"
    for tp in (1.20, 1.50):
        if abs(ratio - tp) <= 0.006:
            return f"price consistent with the +{int(round((tp - 1) * 100))}% take-profit"
    return "exit reason unknown"


def live_ledger_rows(closed: List[dict]) -> List[dict]:
    return [r for r in closed if not str(r.get("exit_reason", "")).startswith("dry_run")]


def match(trips: List[RoundTrip], closed: List[dict]):
    rows = live_ledger_rows(closed)
    used = set()
    matched, missing = [], []
    for rt in trips:
        best, best_gap = None, None
        for i, r in enumerate(rows):
            if i in used or r["symbol"] != rt.symbol:
                continue
            if abs((_f(r["qty"]) or 0) - rt.qty) > QTY_EPS:
                continue
            ts = parse_ts(r["ts"])
            if not (rt.exit_time - MATCH_BEFORE <= ts <= rt.exit_time + MATCH_AFTER):
                continue
            gap = abs((ts - rt.exit_time).total_seconds())
            if best_gap is None or gap < best_gap:
                best, best_gap = i, gap
        if best is None:
            missing.append(rt)
        else:
            used.add(best)
            matched.append((rt, rows[best]))
    orphans = [r for i, r in enumerate(rows) if i not in used]
    return matched, missing, orphans


def proposed_row(rt: RoundTrip) -> dict:
    hint = exit_hint(rt)
    return {
        "symbol": rt.symbol, "qty": rt.qty,
        "entry_price": round(rt.entry_price, 6), "exit_price": round(rt.exit_price, 6),
        "pnl": round(rt.pnl, 4), "pnl_pct": round(rt.pnl_pct, 4),
        "entry_time": rt.entry_time.isoformat() if rt.entry_time else "",
        "exit_time": rt.exit_time.isoformat(),
        "exit_reason": f"reconciled_from_alpaca_fills ({hint})",
        "buy_reason": "reconciled_from_alpaca_fills",
        "news_summary": ("Rebuilt from Alpaca fill records; the bot's ledger had no row for "
                         f"this round trip. Sell order {rt.sell_order_id}; buy order(s) "
                         f"{', '.join(rt.buy_order_ids) or 'not in fill history'}."),
        "source": SOURCE_LABEL,
        "sell_order_id": rt.sell_order_id,
        "buy_order_ids": ";".join(rt.buy_order_ids),
        "entry_incomplete": "yes" if rt.unmatched_sell_qty > QTY_EPS else "no",
    }


def missing_buy_rows(execs: List[Execution], trades: List[dict]) -> List[Execution]:
    known = {r.get("order_id") for r in trades
             if r.get("action") == "buy" and str(r.get("dry_run")).lower() != "true"}
    return [e for e in execs if e.side == "buy" and e.order_id not in known]


def run(data_dir: str, out_dir: str) -> dict:
    def load(name):
        with open(os.path.join(data_dir, name), newline="") as fh:
            return list(csv.DictReader(fh))

    fills, closed, trades = load("fills.csv"), load("closed_trades.csv"), load("trades.csv")
    execs = group_fills(fills)
    trips, open_lots = build_round_trips(execs)
    matched, missing, orphans = match(trips, closed)
    ledger_rows = live_ledger_rows(closed)
    ledger_start = min((parse_ts(r["ts"]) for r in ledger_rows), default=None)
    trades_start = min((parse_ts(r["ts"]) for r in trades
                        if str(r.get("dry_run")).lower() != "true"), default=None)
    in_window = [rt for rt in missing if ledger_start and rt.exit_time >= ledger_start]
    pre_ledger = [rt for rt in missing if not (ledger_start and rt.exit_time >= ledger_start)]
    no_buy_row = missing_buy_rows(execs, trades)
    no_buy_row_in_window = [e for e in no_buy_row if trades_start and e.first_ts >= trades_start]

    os.makedirs(out_dir, exist_ok=True)

    def write_csv(name, rows):
        path = os.path.join(out_dir, name)
        with open(path, "w", newline="") as fh:
            if rows:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            else:
                fh.write("")
        return path

    write_csv("proposed_closed_trades.csv", [proposed_row(rt) for rt in in_window])
    write_csv("pre_ledger_round_trips.csv", [proposed_row(rt) for rt in pre_ledger])
    diffs = []
    for rt, r in matched:
        lep, lxp = _f(r["entry_price"]) or 0.0, _f(r["exit_price"]) or 0.0
        let = r.get("entry_time") or ""
        et_gap_h = ((parse_ts(let) - rt.entry_time).total_seconds() / 3600.0
                    if let and rt.entry_time else None)
        diffs.append({
            "ledger_id": r["id"], "symbol": rt.symbol, "qty": rt.qty,
            "ledger_exit_reason": r["exit_reason"],
            "ledger_entry_price": lep, "fills_entry_price": round(rt.entry_price, 4),
            "entry_price_diff_pct": round((lep - rt.entry_price) / rt.entry_price * 100, 3)
            if rt.entry_price else "",
            "ledger_exit_price": lxp, "fills_exit_price": round(rt.exit_price, 4),
            "exit_price_diff_pct": round((lxp - rt.exit_price) / rt.exit_price * 100, 3)
            if rt.exit_price else "",
            "ledger_pnl": _f(r["pnl"]), "fills_pnl": round(rt.pnl, 2),
            "entry_time_diff_hours": round(et_gap_h, 2) if et_gap_h is not None else "",
            "ledger_written_after_sell_minutes":
                round((parse_ts(r["ts"]) - rt.exit_time).total_seconds() / 60.0, 1),
        })
    write_csv("matched_with_differences.csv", diffs)
    write_csv("ledger_rows_without_fills.csv",
              [{k: r[k] for k in ("id", "ts", "symbol", "qty", "entry_price", "exit_price",
                                  "pnl", "exit_reason")} for r in orphans])
    write_csv("buys_without_trades_row.csv",
              [{"order_id": e.order_id, "symbol": e.symbol, "qty": e.qty,
                "price": round(e.price, 4), "first_fill": e.first_ts.isoformat(),
                "fills": e.fill_count,
                "after_trades_table_started": "yes" if e in no_buy_row_in_window else "no"}
               for e in no_buy_row])
    write_csv("open_at_end_of_fill_history.csv",
              [{"symbol": s, "qty": round(sum(l[0] for l in b), 6),
                "avg_price": round(sum(l[0] * l[1] for l in b) / sum(l[0] for l in b), 4),
                "oldest_lot": min(l[2] for l in b).isoformat()} for s, b in open_lots.items()])

    big_price_diffs = [d for d in diffs
                       if isinstance(d["exit_price_diff_pct"], float) and abs(d["exit_price_diff_pct"]) > 0.5
                       or isinstance(d["entry_price_diff_pct"], float) and abs(d["entry_price_diff_pct"]) > 0.5]
    summary = {
        "fills": len(fills), "executions": len(execs),
        "buy_orders": sum(1 for e in execs if e.side == "buy"),
        "sell_orders": sum(1 for e in execs if e.side == "sell"),
        "round_trips": len(trips),
        "ledger_rows_live": len(ledger_rows),
        "matched": len(matched),
        "missing_after_ledger_start": len(in_window),
        "missing_pnl_after_ledger_start": round(sum(rt.pnl for rt in in_window), 2),
        "pre_ledger_round_trips": len(pre_ledger),
        "ledger_rows_without_fills": len(orphans),
        "matched_with_price_diff_over_0_5pct": len(big_price_diffs),
        "buys_without_trades_row": len(no_buy_row),
        "buys_without_trades_row_after_trades_start": len(no_buy_row_in_window),
        "open_symbols_at_end": len(open_lots),
        "ledger_start": ledger_start.isoformat() if ledger_start else None,
        "fill_history": (execs[0].first_ts.isoformat(), execs[-1].last_ts.isoformat())
        if execs else None,
    }
    _write_report(out_dir, summary, in_window, diffs, orphans, big_price_diffs)
    return summary


def _write_report(out_dir, s, missing, diffs, orphans, big):
    L = ["# Ledger reconciliation - DRY RUN", "",
         "Nothing was written to any database. Proposed rows are in "
         "`proposed_closed_trades.csv` and would be labelled "
         f"`{SOURCE_LABEL}` if later approved.", "",
         f"- Fill history: {s['fill_history'][0][:10]} to {s['fill_history'][1][:10]}"
         if s["fill_history"] else "- No fills",
         f"- {s['fills']} fill records -> {s['executions']} orders "
         f"({s['buy_orders']} buys, {s['sell_orders']} sells) -> {s['round_trips']} round trips",
         f"- Ledger (closed_trades, excluding dry-run rows): {s['ledger_rows_live']} rows, "
         f"first written {str(s['ledger_start'])[:10]}",
         f"- Matched to a ledger row: **{s['matched']}**",
         f"- Missing from the ledger since it started: **{s['missing_after_ledger_start']}** "
         f"(combined P/L ${s['missing_pnl_after_ledger_start']:,.2f})",
         f"- Before the ledger existed (informational, not proposed): {s['pre_ledger_round_trips']}",
         f"- Ledger rows with no matching fills: {s['ledger_rows_without_fills']}",
         f"- Matched rows whose entry or exit price differs from fills by >0.5%: "
         f"{s['matched_with_price_diff_over_0_5pct']}",
         f"- Filled buys with no `trades` row: {s['buys_without_trades_row']} "
         f"({s['buys_without_trades_row_after_trades_start']} after the trades table started)",
         f"- Symbols still open at the end of the fill history: {s['open_symbols_at_end']}", "",
         "## Proposed rows (missing round trips)", "",
         "| Symbol | Qty | Entry | Exit | P/L | Entry time | Exit time | Note |",
         "|---|---|---|---|---|---|---|---|"]
    for rt in missing:
        L.append(f"| {rt.symbol} | {rt.qty:g} | {rt.entry_price:.2f} | {rt.exit_price:.2f} | "
                 f"{rt.pnl:,.2f} | {rt.entry_time.isoformat()[:16] if rt.entry_time else '?'} | "
                 f"{rt.exit_time.isoformat()[:16]} | {exit_hint(rt)} |")
    L += ["", "## Matched rows with price differences over 0.5%", ""]
    if big:
        L += ["| Ledger id | Symbol | Entry diff % | Exit diff % | Ledger P/L | Fills P/L |",
              "|---|---|---|---|---|---|"]
        L += [f"| {d['ledger_id']} | {d['symbol']} | {d['entry_price_diff_pct']} | "
              f"{d['exit_price_diff_pct']} | {d['ledger_pnl']} | {d['fills_pnl']} |" for d in big]
    else:
        L.append("None.")
    L += ["", "## Ledger rows with no matching fills", ""]
    if orphans:
        L += ["| Id | Written | Symbol | Qty | Exit reason |", "|---|---|---|---|---|"]
        L += [f"| {r['id']} | {r['ts'][:16]} | {r['symbol']} | {r['qty']} | {r['exit_reason'][:60]} |"
              for r in orphans]
    else:
        L.append("None.")
    with open(os.path.join(out_dir, "REPORT.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="folder with fills/closed_trades/trades CSVs")
    ap.add_argument("--out", required=True, help="folder for the report (created)")
    a = ap.parse_args()
    s = run(a.data, a.out)
    for k, v in s.items():
        print(f"{k}: {v}")
    print(f"Report: {os.path.join(a.out, 'REPORT.md')}")


if __name__ == "__main__":
    main()
