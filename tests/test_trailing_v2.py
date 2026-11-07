"""Stage 5: corrected trailing stop (sticky +3% arming, 7% retracement,
breakeven floor, persisted state, restart recovery). Flag default OFF."""
from collections import Counter
from types import SimpleNamespace

import pytest

from bot.config import ReliabilityConfig
from bot.trading.strategy import CycleStats, SentimentStrategy
from bot.trading.trailing import TrailState, entry_reference, evaluate, merge
from tests.pg_fixture import pg_url  # noqa: F401


def st(entry=100.0, armed=False, peak=0.0):
    return TrailState(entry_ref=entry_reference(entry), entry_price=entry, armed=armed, peak=peak)


# ---- pure rules -----------------------------------------------------------------------------
def test_arms_exactly_at_three_percent_not_before():
    assert not evaluate(st(), 102.99, 0).state.armed
    d = evaluate(st(), 103.0, 0)
    assert d.state.armed and d.newly_armed and not d.exit


def test_arming_is_sticky_after_falling_back_below_three_percent():
    d = evaluate(st(), 103.5, 0)
    d = evaluate(d.state, 101.0, 1)               # back to +1%: still armed, above floor
    assert d.state.armed and not d.exit


def test_breakeven_floor_replaces_minus_4_2_percent_exit():
    d = evaluate(st(), 103.0, 0)                  # armed at the +3% peak
    assert not evaluate(d.state, 100.5, 1).exit   # old spec would hold to 95.79
    e = evaluate(d.state, 100.0, 2)
    assert e.exit and e.stop_level == 100.0 and "breakeven floor" in e.reason


def test_seven_percent_retracement_from_a_higher_peak():
    d = evaluate(st(), 120.0, 0)                  # peak 120, level 111.6
    assert not evaluate(d.state, 112.0, 1).exit
    e = evaluate(d.state, 111.6, 2)
    assert e.exit and round(e.stop_level, 2) == 111.6 and "7% below the peak" in e.reason


def test_never_exits_while_unarmed_even_on_a_drop():
    d = evaluate(st(), 102.0, 0)
    assert not evaluate(d.state, 92.0, 1).exit    # the -10% broker stop handles losses


def test_level_never_below_entry_and_gap_is_reported():
    d = evaluate(st(), 103.0, 0)
    e = evaluate(d.state, 97.0, 1)                # gapped through breakeven between checks
    assert e.exit and e.stop_level == 100.0 and "gapped" in e.reason


def test_peak_only_rises_and_merge_is_sticky():
    a = TrailState("100.0000", 100.0, armed=True, armed_at=5, peak=110)
    b = TrailState("100.0000", 100.0, armed=False, armed_at=None, peak=115)
    m = merge(a, None, b)
    assert m.armed and m.peak == 115 and m.armed_at == 5
    assert evaluate(m, 112, 9).state.peak == 115


# ---- strategy integration ---------------------------------------------------------------------
class Rec:
    def __init__(self, readable=True):
        self.rows, self.readable, self.upserts, self.cleared = {}, readable, [], []

    def get_trailing_state(self, sym):
        if not self.readable:
            return False, None
        return True, self.rows.get(sym)

    def upsert_trailing_state(self, sym, ref, entry, armed, armed_at, peak, peak_source="observed"):
        self.upserts.append((sym, armed, peak, peak_source))
        if not self.readable:
            return
        old = self.rows.get(sym)
        if old and old["entry_ref"] == ref:
            armed, peak = old["armed"] or armed, max(old["peak"], peak)
            armed_at = old["armed_at"] or armed_at
        self.rows[sym] = dict(entry_ref=ref, entry_price=entry, armed=armed, armed_at=armed_at, peak=peak)

    def get_last_buy_trade(self, sym):
        return None


def strat(prices, rec=None, v2=True, bars_high=None, entry=100.0):
    s = SentimentStrategy.__new__(SentimentStrategy)
    sells = []
    s.cfg = SimpleNamespace(
        risk=SimpleNamespace(trailing_stop_enabled=True, trailing_stop_activation_pct=3.0,
                             trailing_stop_pct=7.0),
        reliability=ReliabilityConfig(trailing_v2_enabled=v2))
    it = iter(prices)
    s.broker = SimpleNamespace(latest_price=lambda sym: next(it),
                               max_high_since=lambda sym, since: bars_high)
    s._position_details_cache = {"AAPL": SimpleNamespace(avg_entry_price=entry)}
    s.recorder = rec or Rec()
    s._resolve_lot = lambda sym: {"entry_time": 1_762_000_000.0}
    s._do_sell = lambda sym, sent, reason, entry_path: sells.append(reason) or True
    s.state = SimpleNamespace(mark_exit=lambda sym: None)
    return s, sells


def step(s):
    return s._maybe_trailing_stop_exit("AAPL", None, "sentiment_momentum", Counter({"tech": 1}), CycleStats())


def test_v2_end_to_end_arm_then_breakeven_exit_persisted():
    rec = Rec()
    s, sells = strat([103.2, 101.0, 99.9], rec)
    assert not step(s) and rec.rows["AAPL"]["armed"]
    assert not step(s)
    assert step(s) and len(sells) == 1 and "breakeven floor" in sells[0]


def test_restart_recovers_armed_state_from_db():
    rec = Rec()
    s1, _ = strat([104.0], rec)
    step(s1)                                       # armed, stored
    s2, sells = strat([99.95], rec)                # new process: empty memory
    assert step(s2) and sells                      # still armed -> breakeven exit


def test_db_outage_uses_memory_and_never_disarms():
    rec = Rec()
    s, sells = strat([104.0, 102.0, 100.0], rec)
    step(s)
    rec.readable = False                           # DB down
    assert not step(s)
    assert step(s) and sells                       # memory kept it armed


def test_both_lost_rebuilds_peak_from_bars():
    rec = Rec(readable=False)
    s, sells = strat([100.0], rec, bars_high=104.0)    # was +4% while we were blind
    assert step(s) and sells                       # armed from bars; at the floor -> exit


def test_stale_state_from_previous_position_is_ignored():
    rec = Rec()
    rec.rows["AAPL"] = dict(entry_ref=entry_reference(80.0), entry_price=80.0, armed=True,
                            armed_at=1.0, peak=200.0)
    s, sells = strat([101.0], rec)                 # new position at 100: not armed
    assert not step(s) and not sells
    assert rec.rows["AAPL"]["entry_ref"] == entry_reference(100.0) and not rec.rows["AAPL"]["armed"]


def test_flag_off_keeps_the_original_rule():
    s, sells = strat([103.0], v2=False)
    s.recorder = SimpleNamespace(get_last_buy_trade=lambda sym: {"price": 100.0},
                                 update_position_peak=lambda *a: None,
                                 get_position_peak=lambda sym: None)
    assert not step(s)
    with pytest.raises(AttributeError):
        s._trail_mem                               # v2 never touched


def test_trailing_state_sql_is_sticky_and_resets_for_new_position(pg_url):
    import psycopg2

    from bot.persistence.db import Recorder

    r = Recorder(database_url=pg_url)
    r.upsert_trailing_state("ZZZ", "100.0000", 100.0, True, 1_762_000_000.0, 105.0)
    r.upsert_trailing_state("ZZZ", "100.0000", 100.0, False, None, 103.0)   # lower peak, not armed
    ok, row = r.get_trailing_state("ZZZ")
    assert ok and row["armed"] and row["peak"] == 105.0 and row["armed_at"]
    r.upsert_trailing_state("ZZZ", "90.0000", 90.0, False, None, 91.0)      # new position
    ok, row = r.get_trailing_state("ZZZ")
    assert not row["armed"] and row["peak"] == 91.0
    r.clear_trailing_state("ZZZ")
    assert r.get_trailing_state("ZZZ") == (True, None)
    with psycopg2.connect(pg_url) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM trailing_state")
        assert cur.fetchone()[0] == 0


# ---- reconstruction safeguards (review 7 Nov) -----------------------------------------------
from datetime import datetime, timedelta, timezone  # noqa: E402

from bot.trading.trailing import completed_session_high  # noqa: E402


def bar(local_iso, high):
    from zoneinfo import ZoneInfo
    ts = datetime.fromisoformat(local_iso).replace(tzinfo=ZoneInfo("America/New_York"))
    return SimpleNamespace(timestamp=ts.astimezone(timezone.utc), high=high)


def test_reconstruction_uses_completed_regular_session_bars_only():
    now = bar("2026-11-09T11:07", 0).timestamp
    bars = [bar("2026-11-09T08:00", 150.0),   # pre-market: ignored
            bar("2026-11-09T09:30", 104.0),   # regular session, complete
            bar("2026-11-09T10:45", 106.0),   # complete (ends 11:00)
            bar("2026-11-09T11:00", 120.0),   # still forming at 11:07: ignored
            bar("2026-11-08T12:00", 130.0)]   # Sunday: ignored
    assert completed_session_high(bars, now) == 106.0
    assert completed_session_high([], now) is None


def test_reconstructed_peak_is_labelled_and_live_observation_replaces_label():
    rec = Rec(readable=False)
    s, _ = strat([101.0, 106.0], rec, bars_high=105.0)
    step(s)
    assert s._trail_mem["AAPL"].peak == 105.0 and s._trail_mem["AAPL"].peak_source == "reconstructed"
    step(s)
    assert s._trail_mem["AAPL"].peak == 106.0 and s._trail_mem["AAPL"].peak_source == "observed"


def test_reconstruction_never_lowers_a_recorded_peak():
    m = merge(TrailState("100.0000", 100.0, armed=True, peak=112.0),
              TrailState("100.0000", 100.0, peak=108.0, peak_source="reconstructed"))
    assert m.peak == 112.0 and m.peak_source == "observed"
    assert evaluate(m, 109.0, 1).state.peak == 112.0


def test_below_trigger_at_next_scan_attempts_exit_not_hold():
    d = evaluate(st(), 110.0, 0)                  # armed, level 102.3
    e = evaluate(d.state, 95.0, 1)                # discovered well below the trigger
    assert e.exit and e.state.armed               # attempts the exit; never disarms


def test_peak_source_sql(pg_url):
    from bot.persistence.db import Recorder

    r = Recorder(database_url=pg_url)
    r.upsert_trailing_state("YYY", "100.0000", 100.0, False, None, 105.0, "reconstructed")
    r.upsert_trailing_state("YYY", "100.0000", 100.0, False, None, 104.0, "observed")   # lower: keeps label
    assert r.get_trailing_state("YYY")[1]["peak_source"] == "reconstructed"
    r.upsert_trailing_state("YYY", "100.0000", 100.0, False, None, 107.0, "observed")
    ok, row = r.get_trailing_state("YYY")
    assert row["peak"] == 107.0 and row["peak_source"] == "observed"
    r.clear_trailing_state("YYY")
