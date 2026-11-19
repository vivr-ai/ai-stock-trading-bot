"""DQ3 (review 19 Nov): buy rows record Alpaca's actual fill, not the
decision-time price. Covers the helper, the strategy buy path, the staging
test-entry path, the provisional -> confirmed sync, and the SQL against a
throwaway Postgres (no duplicates, confirmed rows never overwritten)."""
from types import SimpleNamespace

import pytest

from bot.state import BotState
from bot.trading import fills as fl
from bot.trading.alpaca_client import BrokerSnapshot
from bot.trading.risk import OrderPlan, RiskDecision
from bot.trading.strategy import CycleStats, SentimentStrategy
from tests.pg_fixture import pg_url  # noqa: F401 - fixture


# ---- a broker whose order fills in parts over successive reads ----------------------------
class PartialFillBroker:
    """Each read of the order applies the next partial fill; Alpaca-style
    filled_avg_price = quantity-weighted average of every fill so far."""

    def __init__(self, parts, final_status="filled", order_id="o1"):
        self.parts, self.final_status, self.order_id = list(parts), final_status, order_id
        self.done, self.reads, self.buys = [], 0, []

    def order_snapshot(self, oid):
        assert oid == self.order_id
        self.reads += 1
        if self.parts:
            self.done.append(self.parts.pop(0))
        q = sum(p[0] for p in self.done)
        avg = (sum(p[0] * p[1] for p in self.done) / q) if q else None
        status = self.final_status if not self.parts else ("partially_filled" if q else "new")
        return BrokerSnapshot(True, {"id": oid, "status": status, "filled_qty": str(q),
                                     "filled_avg_price": None if avg is None else str(avg)}, "")

    def submit_bracket_buy(self, sym, qty, stop, tp, client_order_id=None):
        self.buys.append((sym, qty, client_order_id))
        return SimpleNamespace(order_id=self.order_id, status="pending_new")


def resolve(broker, req_price=100.0, req_qty=30, timeout=5.0):
    t = [0.0]
    return fl.resolve_entry_fill(broker, "o1", req_price, req_qty, timeout_s=timeout, poll_s=0.5,
                                 sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])


def test_full_fill_at_a_different_price_records_the_fill():
    f = resolve(PartialFillBroker([(30, 101.27)]), req_price=100.0)
    assert f.confirmed and f.status == "filled"
    assert f.price == pytest.approx(101.27) and f.qty == 30
    assert f.requested_price == 100.0 and f.price != f.requested_price


def test_multiple_partial_fills_give_the_broker_aggregate():
    parts = [(10, 100.00), (15, 100.10), (5, 100.40)]
    b = PartialFillBroker(parts)
    f = resolve(b, req_price=99.95, req_qty=30)
    expected = (10 * 100.00 + 15 * 100.10 + 5 * 100.40) / 30
    assert f.confirmed and b.reads == 3
    assert f.qty == 30 and f.price == pytest.approx(expected)


def test_final_partial_fill_records_only_the_filled_quantity():
    b = PartialFillBroker([(12, 50.10)], final_status="canceled")
    f = resolve(b, req_price=50.0, req_qty=20)
    assert f.confirmed and f.status == "canceled"
    assert f.qty == 12 and f.price == pytest.approx(50.10)


def test_nothing_filled_is_final_with_zero_quantity():
    b = PartialFillBroker([(0, 0.0)], final_status="rejected")
    f = resolve(b)
    assert f.confirmed and f.qty == 0 and not f.filled


def test_not_final_by_the_deadline_is_provisional_with_requested_figures():
    b = PartialFillBroker([(10, 100.0)] + [(0, 0.0)] * 50)      # never finishes within 5 s
    f = resolve(b, req_price=99.0, req_qty=30, timeout=5.0)
    assert not f.confirmed and f.status == "partially_filled"
    assert f.price == 99.0 and f.qty == 30 and f.filled


def test_broker_without_order_lookup_is_provisional_immediately():
    f = fl.resolve_entry_fill(object(), "o1", 10.0, 1, timeout_s=10.0)
    assert not f.confirmed and f.status == "unknown" and f.price == 10.0


def test_status_values_from_the_sdk_enum_are_understood():
    f = fl.fill_from_order({"status": "OrderStatus.FILLED", "filled_qty": "1",
                            "filled_avg_price": "51.53"}, 51.54, 1)
    assert f.confirmed and f.price == 51.53


# ---- strategy live buy path -----------------------------------------------------------------
class Rec:
    def __init__(self):
        self.trades, self.decisions, self.notes, self.peaks, self.ckpts = [], [], [], [], []

    def record_trade(self, **k):
        self.trades.append(k)

    def record_decision(self, **k):
        self.decisions.append(k)

    def record_notification(self, **k):
        self.notes.append(k)

    def reset_position_peak(self, sym, p):
        self.peaks.append((sym, p))

    def reset_stagnation_checkpoint(self, sym, p):
        self.ckpts.append((sym, p))


def fake_strategy(tmp_path, broker, dry_run=False, plan_price=100.0, qty=30):
    plan = OrderPlan(symbol="AAPL", qty=qty, price=plan_price, notional=plan_price * qty,
                     stop_price=90.0, take_profit_price=120.0)
    rec = Rec()
    s = SimpleNamespace(
        db_gate=None, entry_blockers=(), ENTRY_FILL_WAIT_S=0.0,
        cfg=SimpleNamespace(risk=SimpleNamespace(dry_run=dry_run), strategy=SimpleNamespace(buy_threshold=8.0)),
        risk=SimpleNamespace(evaluate=lambda *a, **k: RiskDecision(True, "ok", plan)),
        broker=broker, recorder=rec, state=BotState(str(tmp_path / "state.json")),
        trade_logger=SimpleNamespace(log=lambda *a, **k: rec.decisions.append(("csv", a, k))),
        version_provider=SimpleNamespace(current_version=lambda: "v3"),
    )
    return s, rec


def sentiment():
    return SimpleNamespace(score=9.0, label="positive", article_count=6, rationale="r",
                           positive_count=3, negative_count=0)


def do_buy(s):
    return SentimentStrategy._do_buy(s, "AAPL", sentiment(), 100.0, {"portfolio_value": 1e5, "buying_power": 1e5},
                                     0.0, {}, __import__("collections").Counter(), CycleStats(), reason="test")


def test_strategy_buy_records_actual_fill_not_planned_price(tmp_path):
    b = PartialFillBroker([(30, 100.37)])
    s, rec = fake_strategy(tmp_path, b, plan_price=100.00)
    do_buy(s)
    [t] = rec.trades
    assert t["price"] == pytest.approx(100.37) and t["qty"] == 30
    assert t["notional"] == pytest.approx(100.37 * 30)
    assert t["requested_price"] == 100.00 and t["fill_confirmed"] is True and t["status"] == "filled"
    lot = s.state.peek_open("AAPL")
    assert lot["entry_price"] == pytest.approx(100.37) and lot["qty"] == 30
    assert rec.peaks == [("AAPL", pytest.approx(100.37))] and rec.ckpts == [("AAPL", pytest.approx(100.37))]
    csv_row = [d for d in rec.decisions if isinstance(d, tuple)][0]
    assert csv_row[1][3] == pytest.approx(100.37)          # trades.csv price is the fill too


def test_strategy_buy_with_partial_fills_records_aggregate(tmp_path):
    b = PartialFillBroker([(10, 100.00), (20, 100.30)])
    s, rec = fake_strategy(tmp_path, b)
    s.ENTRY_FILL_WAIT_S = 5.0
    import bot.trading.fills as mod
    real = mod.resolve_entry_fill
    try:
        t = [0.0]
        mod_resolve = lambda *a, **k: real(*a, **{**k, "sleep": lambda x: t.__setitem__(0, t[0] + x),  # noqa: E731
                                                    "clock": lambda: t[0]})
        import bot.trading.strategy as st
        st.resolve_entry_fill = mod_resolve
        do_buy(s)
    finally:
        st.resolve_entry_fill = real
    [tr] = rec.trades
    assert tr["qty"] == 30 and tr["price"] == pytest.approx((10 * 100.0 + 20 * 100.3) / 30)


def test_strategy_buy_not_finished_is_provisional_then_completed_by_sync(tmp_path):
    b = PartialFillBroker([(0, 0.0)])                      # first read: still "new"... then finishes
    b.parts = [(0, 0.0), (30, 100.42)]
    s, rec = fake_strategy(tmp_path, b)
    do_buy(s)                                              # ENTRY_FILL_WAIT_S = 0 -> one read only
    [t] = rec.trades
    assert t["fill_confirmed"] is False and t["price"] == 100.0 and t["qty"] == 30
    assert s.state.peek_open("AAPL")["entry_price"] == 100.0

    confirmed = []
    s.recorder.pending_entry_fills = lambda: [{"order_id": "o1", "symbol": "AAPL",
                                               "requested_price": 100.0, "qty": 30}]
    s.recorder.confirm_entry_fill = lambda **k: confirmed.append(k)
    SentimentStrategy._sync_pending_entry_fills(s)
    assert confirmed == [{"order_id": "o1", "price": pytest.approx(100.42), "qty": 30, "status": "filled"}]
    assert s.state.peek_open("AAPL")["entry_price"] == pytest.approx(100.42)
    assert len(rec.trades) == 1                            # completed in place, no second row


def test_sync_skips_orders_that_are_still_open(tmp_path):
    b = PartialFillBroker([(5, 100.0), (25, 100.0)])
    s, rec = fake_strategy(tmp_path, b)
    calls = []
    s.recorder.pending_entry_fills = lambda: [{"order_id": "o1", "symbol": "AAPL", "requested_price": 99.0, "qty": 30}]
    s.recorder.confirm_entry_fill = lambda **k: calls.append(k)
    SentimentStrategy._sync_pending_entry_fills(s)        # partially filled -> nothing written
    assert calls == []


def test_dry_run_buy_keeps_planned_figures_and_never_reads_the_broker(tmp_path):
    class NoReads(PartialFillBroker):
        def order_snapshot(self, oid):
            raise AssertionError("dry run must not look orders up")
    s, rec = fake_strategy(tmp_path, NoReads([]), dry_run=True)
    do_buy(s)
    [t] = rec.trades
    assert t["price"] == 100.0 and t["status"] == "dry_run" and t.get("fill_confirmed") is None
    SentimentStrategy._sync_pending_entry_fills(s)        # dry run: no-op


def test_rejected_with_no_fill_opens_no_lot(tmp_path):
    s, rec = fake_strategy(tmp_path, PartialFillBroker([(0, 0.0)], final_status="rejected"))
    do_buy(s)
    assert rec.trades[0]["qty"] == 0 and rec.trades[0]["fill_confirmed"] is True
    assert s.state.peek_open("AAPL") is None and rec.peaks == []


# ---- staging test-entry path ----------------------------------------------------------------
def test_test_entry_records_actual_fill(tmp_path):
    from tests.test_test_entries import make
    t = make(tmp_path)
    t.b.order_snapshot = lambda oid: BrokerSnapshot(True, {"id": oid, "status": "filled", "filled_qty": "1",
                                                           "filled_avg_price": "45.97"}, "")
    assert t.r.tick() == "entered"
    trade = [k for kind, k in [x for x in t.sold if x[0] == "trade"]][0]
    assert trade["price"] == pytest.approx(45.97) and trade["requested_price"] == 46.0
    assert trade["fill_confirmed"] is True and trade["qty"] == 1
    fill_ev = [pl for k, pl in t.journal if k == "test_entry_fill"][0]
    assert fill_ev["fill_price"] == pytest.approx(45.97) and fill_ev["confirmed"] is True


# ---- SQL against a throwaway Postgres -------------------------------------------------------
@pytest.fixture
def recorder(pg_url):  # noqa: F811
    from bot.persistence.db import Recorder
    import psycopg2
    with psycopg2.connect(pg_url) as c, c.cursor() as cur:
        cur.execute("TRUNCATE trades")
    r = Recorder(database_url=pg_url)
    r.RETRY_DELAYS_S = ()            # a failing write must fail the test, not sleep
    return r, pg_url


def _rows(url):
    import psycopg2
    with psycopg2.connect(url) as c, c.cursor() as cur:
        cur.execute("SELECT order_id, symbol, qty, price, notional, status, requested_price, fill_confirmed "
                    "FROM trades ORDER BY id")
        return cur.fetchall()


def _buy(r, oid, price, qty, confirmed, status="pending_new", req=100.0):
    r.record_trade(action="buy", symbol="AAPL", qty=qty, price=price, notional=price * qty, sentiment=None,
                   stop_price=90.0, take_profit=120.0, reason="t", rationale="", dry_run=False,
                   order_id=oid, status=status, requested_price=req, fill_confirmed=confirmed)


def test_sql_confirm_completes_only_provisional_rows(recorder):
    r, url = recorder
    _buy(r, "prov", 100.0, 30, False)
    _buy(r, "done", 101.11, 30, True, status="filled")
    r.record_trade(action="buy", symbol="MSFT", qty=5, price=400.0, notional=2000.0, sentiment=None,
                   stop_price=0, take_profit=0, reason="legacy", rationale="", dry_run=False,
                   order_id="legacy", status="pending_new")           # NULL fill_confirmed (older row)
    pend = r.pending_entry_fills()
    assert [p["order_id"] for p in pend] == ["prov"]

    r.confirm_entry_fill(order_id="prov", price=100.42, qty=30, status="filled")
    r.confirm_entry_fill(order_id="prov", price=99.0, qty=30, status="filled")     # rerun: no effect
    r.confirm_entry_fill(order_id="done", price=100.0, qty=30, status="filled")    # confirmed: untouched
    r.confirm_entry_fill(order_id="legacy", price=1.0, qty=1, status="filled")     # legacy: untouched
    rows = {row[0]: row for row in _rows(url)}
    assert len(rows) == 3                                                          # no duplicates
    assert float(rows["prov"][3]) == pytest.approx(100.42) and rows["prov"][7] is True
    assert float(rows["prov"][4]) == pytest.approx(100.42 * 30)
    assert float(rows["prov"][6]) == 100.0                                          # requested kept
    assert float(rows["done"][3]) == pytest.approx(101.11)
    assert float(rows["legacy"][3]) == 400.0 and rows["legacy"][7] is None
    assert r.pending_entry_fills() == []


# ---- t03 correction plan (insert-only audit rows) ----------------------------------------------
def test_t03_correction_plan_documents_original_fill_and_corrected_values():
    import importlib.util, json, os
    spec = importlib.util.spec_from_file_location(
        "corr", os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "ledger_entry_fill_correction.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    order = {"id": "cc2c3411-ac18-48ef-b61d-83945b73f50b", "client_order_id": "sTEST-CSX-t03",
             "status": "filled", "filled_avg_price": "51.53", "filled_qty": "1",
             "filled_at": "2026-11-18T15:30:15.592709Z"}
    trade = {"id": 7, "symbol": "CSX", "price": 51.54, "requested_price": None}
    closed = {"id": 3, "qty": 1.0, "entry_price": 51.54, "exit_price": 51.48, "pnl": -0.06}
    rows = m.plan_for(order, trade, closed, "2026-11-19T02:00:00+00:00", "snapshot")
    assert [r["field"] for r in rows] == ["trades.price", "closed_trades.pnl"]
    p, q = rows
    assert (p["ledger_value"], p["broker_value"]) == ("51.54", "51.53")
    assert (q["ledger_value"], q["broker_value"]) == ("-0.06", "-0.05")
    assert p["correction_key"] == "entry_fill:cc2c3411-ac18-48ef-b61d-83945b73f50b:trades.price"
    d = json.loads(q["detail"])
    assert d["corrected_value"] == -0.05 and d["recorded_pnl"] == -0.06 and d["reason"].startswith("DQ3")
    assert d["alpaca_filled_avg_price"] == 51.53 and d["evidence"] == "snapshot"
    # a buy that already matches its fill produces nothing
    assert m.plan_for({**order, "filled_avg_price": "51.54"}, trade, closed, "t", "e") == []


def test_correction_sql_is_insert_only_and_idempotent(pg_url):  # noqa: F811
    import importlib.util, os
    import psycopg2, psycopg2.extras
    spec = importlib.util.spec_from_file_location(
        "corr2", os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "ledger_entry_fill_correction.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    order = {"id": "oX", "status": "filled", "filled_avg_price": "51.53", "filled_qty": "1"}
    rows = m.plan_for(order, {"id": 1, "symbol": "CSX", "price": 51.54}, {"id": 1, "qty": 1, "entry_price": 51.54,
                                                                         "exit_price": 51.48, "pnl": -0.06}, "t", "e")
    sql = ("INSERT INTO ledger_corrections (correction_key, closed_trade_id, symbol, kind, field, ledger_value, "
           "broker_value, detail, broker_order_ids, source) VALUES (%(correction_key)s, %(closed_trade_id)s, "
           "%(symbol)s, %(kind)s, %(field)s, %(ledger_value)s, %(broker_value)s, %(detail)s, %(broker_order_ids)s, "
           "%(source)s) ON CONFLICT (correction_key) DO NOTHING")
    with psycopg2.connect(pg_url) as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("DELETE FROM ledger_corrections WHERE source = %s", (m.SOURCE,))
        before = m._checksum(cur, "trades"), m._checksum(cur, "closed_trades")
        for _ in range(2):
            for r in rows:
                cur.execute(sql, r)
        cur.execute("SELECT field, ledger_value, broker_value FROM ledger_corrections WHERE source=%s ORDER BY id",
                    (m.SOURCE,))
        got = [tuple(x.values()) for x in cur.fetchall()]
        assert got == [("trades.price", "51.54", "51.53"), ("closed_trades.pnl", "-0.06", "-0.05")]
        assert (m._checksum(cur, "trades"), m._checksum(cur, "closed_trades")) == before
