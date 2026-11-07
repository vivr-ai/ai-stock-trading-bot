"""Read-only export of the three inputs the Stage 3 reconciliation needs:
closed_trades and trades (from Postgres) and FILL activities (from Alpaca).

Read-only by construction: one READ ONLY database transaction with SELECTs
only, and HTTP GETs only against Alpaca. Writes CSVs to --out.

Usage (on your Mac, with the PRODUCTION values; nothing is changed):
    export DATABASE_PUBLIC_URL='...'   # Railway -> production Postgres -> Variables
    export ALPACA_API_KEY='...'        # production paper keys
    export ALPACA_SECRET_KEY='...'
    python3 scripts/ledger_export_readonly.py --out ledger_export
"""
from __future__ import annotations

import argparse
import csv
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="ledger_export")
    ap.add_argument("--alpaca-base", default="https://paper-api.alpaca.markets")
    a = ap.parse_args()
    db = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not (db and key and secret):
        print("Set DATABASE_PUBLIC_URL, ALPACA_API_KEY and ALPACA_SECRET_KEY first.", file=sys.stderr)
        return 2
    import psycopg2
    import requests

    os.makedirs(a.out, exist_ok=True)
    conn = psycopg2.connect(db, connect_timeout=15)
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            for name in ("closed_trades", "trades"):
                cur.execute(f"SELECT * FROM {name} ORDER BY id")
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
                with open(os.path.join(a.out, f"{name}.csv"), "w", newline="") as fh:
                    w = csv.writer(fh)
                    w.writerow(cols)
                    w.writerows(rows)
                print(f"{name}: {len(rows)} rows")
    finally:
        conn.rollback()
        conn.close()

    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    acct = requests.get(f"{a.alpaca_base}/v2/account", headers=headers, timeout=30)
    acct.raise_for_status()
    number = str(acct.json().get("account_number", ""))
    print(f"Alpaca account: {'*' * max(0, len(number) - 4)}{number[-4:]}")
    fills, token = [], None
    while True:
        params = {"direction": "asc", "page_size": 100}
        if token:
            params["page_token"] = token
        r = requests.get(f"{a.alpaca_base}/v2/account/activities/FILL", headers=headers,
                         params=params, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        fills.extend(batch)
        token = batch[-1]["id"]
        if len(batch) < 100:
            break
    cols = ["id", "transaction_time", "symbol", "side", "qty", "price", "order_id", "type"]
    with open(os.path.join(a.out, "fills.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for f in fills:
            w.writerow([f.get(c) for c in cols])
    print(f"fills: {len(fills)}")
    print(f"Done. Files are in {os.path.abspath(a.out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
