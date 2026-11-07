"""Stage 3 apply: insert-only, idempotent, existing rows provably unchanged.
Runs against a throwaway Postgres (never a real database)."""
import csv
import importlib.util
import os
import pathlib
import sys

import psycopg2
import pytest

from tests.pg_fixture import pg_url  # noqa: F401 - fixture

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ledger_reconcile_apply", ROOT / "scripts" / "ledger_reconcile_apply.py")
la = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = la
_spec.loader.exec_module(la)

REAL = "/mnt/user-data/uploads/ai-stock-trading-bot/research/sentiment_exit_audit/data"
COLS = ["id", "ts", "symbol", "qty", "entry_price", "exit_price", "pnl", "pnl_pct", "exit_reason",
        "entry_time", "buy_reason", "news_summary", "sector", "confidence_score", "confidence_label",
        "market_regime", "strategy_version"]


def fill(oid, ts, sym, side, qty, price):
    return {"id": f"x::{oid}", "transaction_time": ts, "symbol": sym, "side": side, "qty": str(qty),
            "price": str(price), "order_id": oid, "type": "fill"}


def write(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def seed(url, rows, schema="public"):
    with psycopg2.connect(url) as c, c.cursor() as cur:
        cur.execute("TRUNCATE closed_trades RESTART IDENTITY; TRUNCATE ledger_corrections RESTART IDENTITY;")
        for r in rows:
            vals = [(r.get(k) or None) for k in COLS]
            cur.execute(f"INSERT INTO closed_trades ({', '.join(COLS)}) VALUES ({', '.join(['%s'] * len(COLS))})",
                        vals)
        cur.execute("SELECT setval(pg_get_serial_sequence('closed_trades','id'), "
                    "coalesce((SELECT max(id) FROM closed_trades), 1))")


def synthetic(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    write(d / "fills.csv", [
        fill("b1", "2026-10-01T13:30:00Z", "AAA", "buy", 5, 10.0),
        fill("s1", "2026-10-02T13:30:00Z", "AAA", "sell", 5, 9.0),     # in ledger
        fill("b2", "2026-10-05T13:30:00Z", "BBB", "buy", 7, 100.0),
        fill("s2", "2026-10-09T13:51:00Z", "BBB", "sell", 7, 90.0),    # missing -> insert
        fill("b3", "2026-10-10T13:30:00Z", "CCC", "buy", 3, 50.0),     # still open
    ])
    write(d / "closed_trades.csv", [{
        "id": "1", "ts": "2026-10-02 14:00:00+00:00", "symbol": "AAA", "qty": "5", "entry_price": "10",
        "exit_price": "9.2", "pnl": "-4", "pnl_pct": "-8", "exit_reason": "sentiment -6.0 < -5.0",
        "entry_time": "2026-10-01 13:30:00+00:00"}])
    write(d / "trades.csv", [{"ts": "2026-09-01 13:00:00+00:00", "action": "buy", "order_id": "b1",
                              "dry_run": "False"}])
    return d


def test_plan_and_apply_synthetic_then_rerun_is_noop(pg_url, tmp_path):
    d = synthetic(tmp_path)
    plan = la.build_plan(str(d))
    assert [r["symbol"] for r in plan["inserts"]] == ["BBB"]
    assert plan["open_positions_excluded"] == ["CCC"]
    kinds = sorted(c["kind"] for c in plan["corrections"])
    assert "price_difference" in kinds          # AAA exit 9.2 vs 9.0
    seed(pg_url, [dict(r) for r in csv.DictReader(open(d / "closed_trades.csv"))])
    r1 = la.apply_plan(plan, pg_url, "scratch")
    assert len(r1["inserted"]) == 1 and r1["existing_rows_unchanged"]
    assert r1["after"]["all_rows"] == r1["before"]["all_rows"] + 1
    with psycopg2.connect(pg_url) as c, c.cursor() as cur:
        cur.execute("SELECT ts, source, broker_sell_order_id, pnl FROM closed_trades WHERE symbol='BBB'")
        ts, source, oid, pnl = cur.fetchone()
    from datetime import timezone as _tz
    assert ts.astimezone(_tz.utc).isoformat().startswith("2026-10-09T13:51"), ts
    assert source == la.SOURCE and oid == "s2"
    assert float(pnl) == -70.0
    r2 = la.apply_plan(plan, pg_url, "scratch")                     # second run
    assert r2["inserted"] == [] and len(r2["skipped_existing"]) == 1 and r2["corrections_added"] == 0
    assert r2["after"]["all_rows"] == r1["after"]["all_rows"]


def test_refuses_production(pg_url, tmp_path, monkeypatch):
    plan = la.build_plan(str(synthetic(tmp_path)))
    with pytest.raises(SystemExit):
        la.apply_plan(plan, pg_url, "production")
    monkeypatch.setenv("BOT_ENVIRONMENT", "production")
    with pytest.raises(SystemExit):
        la.apply_plan(plan, pg_url, "staging")


def test_rolls_back_if_existing_rows_change(pg_url, tmp_path):
    d = synthetic(tmp_path)
    plan = la.build_plan(str(d))
    seed(pg_url, [dict(r) for r in csv.DictReader(open(d / "closed_trades.csv"))])
    with psycopg2.connect(pg_url) as c, c.cursor() as cur:
        # A trigger that tampers with an existing row during the apply.
        cur.execute("""
            CREATE OR REPLACE FUNCTION tamper() RETURNS trigger AS $$
            BEGIN UPDATE closed_trades SET pnl = 999 WHERE id = 1; RETURN NEW; END $$ LANGUAGE plpgsql;
            CREATE TRIGGER tamper AFTER INSERT ON ledger_corrections FOR EACH ROW EXECUTE FUNCTION tamper();
        """)
    try:
        with pytest.raises(RuntimeError, match="rolled back"):
            la.apply_plan(plan, pg_url, "scratch")
        with psycopg2.connect(pg_url) as c, c.cursor() as cur:
            cur.execute("SELECT count(*), max(pnl) FROM closed_trades")
            n, mx = cur.fetchone()
        assert n == 1 and float(mx) == -4.0                         # nothing committed
    finally:
        with psycopg2.connect(pg_url) as c, c.cursor() as cur:
            cur.execute("DROP TRIGGER IF EXISTS tamper ON ledger_corrections")


@pytest.mark.skipif(not os.path.exists(os.path.join(REAL, "fills.csv")), reason="export not present")
def test_real_export_against_a_scratch_copy(pg_url, tmp_path):
    plan = la.build_plan(REAL)
    assert [r["symbol"] for r in plan["inserts"]] == ["META"]
    assert plan["inserts"][0]["sell_order_id"].startswith("cd4c64a4")
    assert sorted(plan["open_positions_excluded"]) == ["JNJ", "KO", "MA", "MRK", "PYPL", "TSLA", "TXN"]
    seed(pg_url, list(csv.DictReader(open(os.path.join(REAL, "closed_trades.csv")))))
    r = la.apply_plan(plan, pg_url, "scratch")
    assert r["before"]["all_rows"] == 35 and r["after"]["all_rows"] == 36
    assert round(r["after"]["all_pnl"] - r["before"]["all_pnl"], 2) == -485.19
    assert r["corrections_added"] == len(plan["corrections"]) == 23
    la.write_reports(plan, str(tmp_path / "out"), r)
    assert (tmp_path / "out" / "before_after.md").exists()
