"""Stage 3 dry-run ledger reconciliation: pure functions only, no DB."""
import csv
import importlib.util
import os
import pathlib
import sys

_spec = importlib.util.spec_from_file_location(
    "ledger_reconcile_dryrun",
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "ledger_reconcile_dryrun.py",
)
lr = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = lr  # dataclasses need the module registered
_spec.loader.exec_module(lr)


def fill(oid, ts, sym, side, qty, price, type_="fill"):
    return {"id": f"x::{oid}::{ts}", "transaction_time": ts, "symbol": sym, "side": side,
            "qty": str(qty), "price": str(price), "order_id": oid, "type": type_}


def test_partial_fills_group_into_one_order_with_vwap():
    ex = lr.group_fills([
        fill("b1", "2026-10-01T13:30:00Z", "AAA", "buy", 4, 10.0, "partial_fill"),
        fill("b1", "2026-10-01T13:30:05Z", "AAA", "buy", 6, 11.0, "fill"),
    ])
    assert len(ex) == 1
    assert ex[0].qty == 10 and abs(ex[0].price - 10.6) < 1e-9 and ex[0].fill_count == 2


def test_fifo_round_trip_and_open_remainder():
    ex = lr.group_fills([
        fill("b1", "2026-10-01T13:30:00Z", "AAA", "buy", 10, 10.0),
        fill("b2", "2026-10-02T13:30:00Z", "AAA", "buy", 10, 12.0),
        fill("s1", "2026-10-03T13:30:00Z", "AAA", "sell", 15, 13.0),
    ])
    trips, open_lots = lr.build_round_trips(ex)
    assert len(trips) == 1
    rt = trips[0]
    assert rt.qty == 15 and abs(rt.entry_price - (10 * 10 + 5 * 12) / 15) < 1e-9
    assert rt.buy_order_ids == ["b1", "b2"] and rt.unmatched_sell_qty == 0
    assert open_lots["AAA"][0][0] == 5


def test_sell_without_buy_history_is_flagged_incomplete():
    trips, _ = lr.build_round_trips(lr.group_fills([
        fill("s1", "2026-10-03T13:30:00Z", "AAA", "sell", 5, 13.0)]))
    assert trips[0].unmatched_sell_qty == 5
    assert lr.proposed_row(trips[0])["entry_incomplete"] == "yes"


def test_match_is_one_to_one_and_respects_window():
    trips, _ = lr.build_round_trips(lr.group_fills([
        fill("b1", "2026-10-01T13:30:00Z", "AAA", "buy", 5, 10.0),
        fill("s1", "2026-10-02T13:30:00Z", "AAA", "sell", 5, 9.0),
        fill("b2", "2026-10-05T13:30:00Z", "AAA", "buy", 5, 10.0),
        fill("s2", "2026-10-06T13:30:00Z", "AAA", "sell", 5, 9.0),
    ]))
    closed = [  # one ledger row, written 30 min after the FIRST sell only
        {"id": "1", "ts": "2026-10-02 14:00:00+00:00", "symbol": "AAA", "qty": "5",
         "entry_price": "10", "exit_price": "9", "pnl": "-5", "exit_reason": "x",
         "entry_time": "2026-10-01 13:30:00+00:00"},
        {"id": "2", "ts": "2026-10-06 14:00:00+00:00", "symbol": "AAA", "qty": "5",
         "entry_price": "0", "exit_price": "0", "pnl": "0", "exit_reason": "dry_run: x",
         "entry_time": ""},
    ]
    matched, missing, orphans = lr.match(trips, closed)
    assert [m[0].sell_order_id for m in matched] == ["s1"]
    assert [m.sell_order_id for m in missing] == ["s2"]   # dry-run row never counts
    assert orphans == []


def test_exit_hint_labels_bracket_stop():
    rt = lr.RoundTrip("AAA", 7, 692.12, 622.81, None,
                      lr.parse_ts("2026-10-27T13:51:00Z"), "s")
    assert "stop" in lr.exit_hint(rt)


def test_run_writes_report_and_never_touches_a_database(tmp_path, monkeypatch):
    import psycopg2  # noqa: F401 - present in the project; must not be used
    monkeypatch.setattr("psycopg2.connect",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("DB used")))
    data = tmp_path / "data"
    data.mkdir()

    def w(name, rows):
        with open(data / name, "w", newline="") as fh:
            wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            wr.writeheader()
            wr.writerows(rows)

    w("fills.csv", [fill("b1", "2026-10-01T13:30:00Z", "AAA", "buy", 5, 10.0),
                    fill("s1", "2026-10-02T13:30:00Z", "AAA", "sell", 5, 9.0)])
    w("closed_trades.csv", [{"id": "1", "ts": "2026-09-01 14:00:00+00:00", "symbol": "ZZZ",
                             "qty": "1", "entry_price": "1", "exit_price": "1", "pnl": "0",
                             "exit_reason": "x", "entry_time": ""}])
    w("trades.csv", [{"ts": "2026-09-01 13:00:00+00:00", "action": "buy", "order_id": "b1",
                      "dry_run": "False"}])
    s = lr.run(str(data), str(tmp_path / "out"))
    assert s["missing_after_ledger_start"] == 1 and s["ledger_rows_without_fills"] == 1
    rows = list(csv.DictReader(open(tmp_path / "out" / "proposed_closed_trades.csv")))
    assert rows[0]["source"] == lr.SOURCE_LABEL and rows[0]["exit_reason"].startswith(
        "reconciled_from_alpaca_fills")
    assert os.path.exists(tmp_path / "out" / "REPORT.md")
