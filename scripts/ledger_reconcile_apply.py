"""Stage 3 - ledger reconciliation: PLAN and (staging/scratch only) APPLY.

Builds on scripts/ledger_reconcile_dryrun.py (same matching rules) and turns
its findings into an insert-only plan:

  * closed_trades: one new row per round trip that Alpaca's fills show but the
    ledger lacks, AFTER the ledger started (e.g. the META stop-out, 27 Oct).
    Explicit timestamps; source='alpaca_fills_reconciliation';
    broker_sell_order_id set (unique index), so the insert can run only once.
  * ledger_corrections: one row per documented difference, keyed by a
    deterministic correction_key so reruns add nothing. Kinds:
      price_difference       ledger entry/exit price differs from fills by >0.5%
      entry_time             ledger entry time >1 h from the fills' first buy
      delayed_execution      a time exit that ran > 2 days after its 10-day limit
      buy_without_trades_row filled buy with no `trades` row (not backfilled)
      pre_ledger_round_trip  round trip completed before the ledger existed
  * Never: an UPDATE or DELETE on existing rows, rows for positions still open,
    or a backfill of the `trades` table.

Commands
  plan   --data DIR --out DIR                      (no database at all)
  apply  --data DIR --out DIR --database-url URL --target staging|scratch
         Runs in ONE transaction: before-snapshot (row count, P/L sum and a
         checksum of every existing row), inserts, after-snapshot, verifies the
         existing rows are byte-for-byte unchanged, then commits - or rolls
         back if anything differs. Writes before_after.md.
  Production is refused by this version (needs separate written approval).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import sys
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("ledger_reconcile_dryrun",
                                               os.path.join(HERE, "ledger_reconcile_dryrun.py"))
dr = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = dr
_spec.loader.exec_module(dr)

SOURCE = dr.SOURCE_LABEL
MAX_HOLD_DAYS = 10
DELAY_TOLERANCE_DAYS = 2


def _load(data_dir, name):
    with open(os.path.join(data_dir, name), newline="") as fh:
        return list(csv.DictReader(fh))


def build_plan(data_dir: str) -> dict:
    fills, closed, trades = (_load(data_dir, n) for n in ("fills.csv", "closed_trades.csv", "trades.csv"))
    execs = dr.group_fills(fills)
    trips, open_lots = dr.build_round_trips(execs)
    matched, missing, _orphans = dr.match(trips, closed)
    live = dr.live_ledger_rows(closed)
    ledger_start = min((dr.parse_ts(r["ts"]) for r in live), default=None)
    trades_start = min((dr.parse_ts(r["ts"]) for r in trades
                        if str(r.get("dry_run")).lower() != "true"), default=None)

    inserts, corrections = [], []
    for rt in missing:
        if ledger_start and rt.exit_time >= ledger_start:
            row = dr.proposed_row(rt)
            row["ts"] = rt.exit_time.isoformat()
            inserts.append(row)
        else:
            corrections.append(dict(
                correction_key=f"pre_ledger:{rt.sell_order_id}", closed_trade_id=None, symbol=rt.symbol,
                kind="pre_ledger_round_trip", field=None, ledger_value=None,
                broker_value=f"qty {rt.qty:g} entry {rt.entry_price:.4f} exit {rt.exit_price:.4f} "
                             f"P/L {rt.pnl:.2f}",
                detail=f"Round trip {rt.entry_time.date() if rt.entry_time else '?'} to "
                       f"{rt.exit_time.date()} completed before the ledger existed; not inserted.",
                broker_order_ids=";".join(rt.buy_order_ids + [rt.sell_order_id])))

    for rt, r in matched:
        rid = int(r["id"])
        ids = ";".join(rt.buy_order_ids + [rt.sell_order_id])
        for fld, ledger_v, broker_v in (("entry_price", r.get("entry_price"), rt.entry_price),
                                        ("exit_price", r.get("exit_price"), rt.exit_price)):
            lv = dr._f(ledger_v)
            if lv and broker_v and abs(lv - broker_v) / broker_v * 100 > 0.5:
                corrections.append(dict(
                    correction_key=f"price:{rid}:{fld}", closed_trade_id=rid, symbol=rt.symbol,
                    kind="price_difference", field=fld, ledger_value=f"{lv:.4f}",
                    broker_value=f"{broker_v:.4f}",
                    detail=f"Ledger {fld} differs from Alpaca fills by {(lv - broker_v) / broker_v * 100:+.2f}%. "
                           f"Ledger P/L {dr._f(r.get('pnl')):.2f}, fills P/L {rt.pnl:.2f}.",
                    broker_order_ids=ids))
        let = r.get("entry_time") or ""
        if let and rt.entry_time and abs((dr.parse_ts(let) - rt.entry_time).total_seconds()) > 3600:
            corrections.append(dict(
                correction_key=f"entry_time:{rid}", closed_trade_id=rid, symbol=rt.symbol, kind="entry_time",
                field="entry_time", ledger_value=let, broker_value=rt.entry_time.isoformat(),
                detail="Ledger entry time differs from the first buy fill by "
                       f"{(dr.parse_ts(let) - rt.entry_time).total_seconds() / 86400:.1f} days.",
                broker_order_ids=ids))
        if str(r.get("exit_reason", "")).startswith("max hold") and rt.entry_time:
            held = (rt.exit_time - rt.entry_time).total_seconds() / 86400
            if held > MAX_HOLD_DAYS + DELAY_TOLERANCE_DAYS:
                corrections.append(dict(
                    correction_key=f"delayed:{rid}", closed_trade_id=rid, symbol=rt.symbol,
                    kind="delayed_execution", field="exit_reason", ledger_value=r.get("exit_reason"),
                    broker_value=f"true hold {held:.1f} days",
                    detail=f"Time exit with a {MAX_HOLD_DAYS}-day limit executed after {held:.1f} days "
                           "(software exits or the database were unavailable).",
                    broker_order_ids=ids))

    for e in dr.missing_buy_rows(execs, trades):
        corrections.append(dict(
            correction_key=f"buy_no_trades_row:{e.order_id}", closed_trade_id=None, symbol=e.symbol,
            kind="buy_without_trades_row", field=None, ledger_value=None,
            broker_value=f"qty {e.qty:g} @ {e.price:.4f} on {e.first_ts.isoformat()}",
            detail=("Filled buy with no `trades` row"
                    + (" (before the trades table started)" if trades_start and e.first_ts < trades_start
                       else "") + ". Documented here; `trades` is not backfilled."),
            broker_order_ids=e.order_id))

    return {
        "inserts": inserts, "corrections": corrections,
        "open_positions_excluded": sorted(open_lots),
        "ledger_start": ledger_start.isoformat() if ledger_start else None,
        "counts": {"fills": len(fills), "round_trips": len(trips), "ledger_rows": len(live),
                   "matched": len(matched)},
    }


# ---- database apply -----------------------------------------------------------
SNAP_SQL = """
SELECT count(*), coalesce(sum(pnl), 0), coalesce(max(id), 0),
       coalesce(md5(string_agg(t::text, '|' ORDER BY id)), '')
FROM closed_trades t WHERE id <= %(max_id)s
"""


def _snapshot(cur, max_id=None):
    if max_id is None:
        cur.execute("SELECT coalesce(max(id), 0) FROM closed_trades")
        max_id = cur.fetchone()[0]
    cur.execute(SNAP_SQL, {"max_id": max_id})
    n, pnl, mx, digest = cur.fetchone()
    cur.execute("SELECT count(*), coalesce(sum(pnl), 0) FROM closed_trades")
    total_n, total_pnl = cur.fetchone()
    cur.execute("SELECT count(*) FROM ledger_corrections")
    return {"existing_rows": n, "existing_pnl": float(pnl), "max_id": mx, "checksum": digest,
            "all_rows": total_n, "all_pnl": float(total_pnl), "corrections": cur.fetchone()[0]}


INSERT_TRADE = """
INSERT INTO closed_trades (ts, symbol, qty, entry_price, exit_price, pnl, pnl_pct, exit_reason,
                           entry_time, buy_reason, news_summary, source, broker_sell_order_id)
VALUES (%(ts)s, %(symbol)s, %(qty)s, %(entry_price)s, %(exit_price)s, %(pnl)s, %(pnl_pct)s,
        %(exit_reason)s, %(entry_time)s, %(buy_reason)s, %(news_summary)s, %(source)s,
        %(sell_order_id)s)
ON CONFLICT (broker_sell_order_id) WHERE broker_sell_order_id IS NOT NULL DO NOTHING
RETURNING id
"""

INSERT_CORR = """
INSERT INTO ledger_corrections (correction_key, closed_trade_id, symbol, kind, field, ledger_value,
                                broker_value, detail, broker_order_ids)
VALUES (%(correction_key)s, %(closed_trade_id)s, %(symbol)s, %(kind)s, %(field)s, %(ledger_value)s,
        %(broker_value)s, %(detail)s, %(broker_order_ids)s)
ON CONFLICT (correction_key) DO NOTHING
RETURNING id
"""


def apply_plan(plan: dict, database_url: str, target: str, psycopg2_module=None) -> dict:
    if target not in ("staging", "scratch"):
        raise SystemExit(f"Refusing target {target!r}: only staging or scratch databases. Production "
                         "reconciliation needs separate written approval and a later version of this script.")
    if os.environ.get("BOT_ENVIRONMENT", "").lower() == "production":
        raise SystemExit("BOT_ENVIRONMENT=production: refusing to apply.")
    if psycopg2_module is None:
        import psycopg2 as psycopg2_module  # noqa: N813
    conn = psycopg2_module.connect(database_url, connect_timeout=10)
    try:
        with conn:
            with conn.cursor() as cur:
                before = _snapshot(cur)
                inserted, skipped = [], []
                for r in plan["inserts"]:
                    params = dict(r)
                    params["entry_time"] = r["entry_time"] or None
                    params["source"] = SOURCE
                    cur.execute(INSERT_TRADE, params)
                    row = cur.fetchone()
                    (inserted if row else skipped).append((row[0] if row else None, r["symbol"],
                                                           r["sell_order_id"]))
                corr_new = 0
                for c in plan["corrections"]:
                    cur.execute(INSERT_CORR, c)
                    corr_new += 1 if cur.fetchone() else 0
                after = _snapshot(cur, before["max_id"])
                if (after["existing_rows"], after["checksum"]) != (before["existing_rows"], before["checksum"]):
                    raise RuntimeError("Existing closed_trades rows changed during apply - rolled back.")
                after_all = _snapshot(cur)
        return {"before": before, "after": after_all, "inserted": inserted, "skipped_existing": skipped,
                "corrections_added": corr_new, "existing_rows_unchanged": True}
    finally:
        conn.close()


def write_reports(plan: dict, out_dir: str, result: dict = None) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "plan.json"), "w") as fh:
        json.dump(plan, fh, indent=2, default=str)
    kinds = {}
    for c in plan["corrections"]:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    L = ["# Ledger reconciliation plan", "",
         f"- Inserts into closed_trades: **{len(plan['inserts'])}**",
         *[f"  - {r['symbol']} qty {r['qty']:g}, entry {r['entry_price']:.2f} ({r['entry_time'][:16]}), "
           f"exit {r['exit_price']:.2f} ({r['ts'][:16]}), P/L {r['pnl']:.2f}, sell order "
           f"{r['sell_order_id']}" for r in plan["inserts"]],
         f"- ledger_corrections rows: **{len(plan['corrections'])}** "
         + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())),
         f"- Open positions excluded: {', '.join(plan['open_positions_excluded']) or 'none'}",
         "- `trades` table: not modified. Existing closed_trades rows: never modified.", ""]
    if result:
        b, a = result["before"], result["after"]
        L += ["## Before and after (one transaction)", "",
              "| | Before | After |", "|---|---|---|",
              f"| closed_trades rows | {b['all_rows']} | {a['all_rows']} |",
              f"| closed_trades P/L sum | {b['all_pnl']:.2f} | {a['all_pnl']:.2f} |",
              f"| ledger_corrections rows | {b['corrections']} | {a['corrections']} |",
              f"| checksum of the {b['existing_rows']} pre-existing rows | `{b['checksum']}` | unchanged |",
              "",
              f"Inserted: {', '.join(f'{s} (id {i})' for i, s, _ in result['inserted']) or 'nothing'}. "
              f"Already present (skipped): {', '.join(s for _, s, _ in result['skipped_existing']) or 'none'}. "
              f"Corrections added: {result['corrections_added']}.", ""]
    with open(os.path.join(out_dir, "before_after.md" if result else "PLAN.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser(description="Stage 3 ledger reconciliation (plan / apply)")
    ap.add_argument("command", choices=["plan", "apply"])
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--database-url")
    ap.add_argument("--target", choices=["staging", "scratch", "production"])
    a = ap.parse_args()
    plan = build_plan(a.data)
    if a.command == "plan":
        write_reports(plan, a.out)
        print(f"Plan: {len(plan['inserts'])} insert(s), {len(plan['corrections'])} correction(s). "
              f"See {os.path.join(a.out, 'PLAN.md')}")
        return
    if not a.database_url or not a.target:
        raise SystemExit("apply needs --database-url and --target")
    result = apply_plan(plan, a.database_url, a.target)
    write_reports(plan, a.out, result)
    print(json.dumps({k: v for k, v in result.items() if k in ("inserted", "corrections_added")}, default=str))


if __name__ == "__main__":
    main()
