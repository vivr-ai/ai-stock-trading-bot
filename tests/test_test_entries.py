"""Review 11 Nov item 5: staging-only TEST entries."""
import os
from types import SimpleNamespace

import pytest

from tests.test_main_wiring import cfg_env  # noqa: F401 - fixture

from bot.reliability import test_entries as te
from bot.trading.alpaca_client import BrokerSnapshot, OrderRejected

STAGING = {"BOT_ENVIRONMENT": "staging", "STAGING_TEST_ENTRY_REQUEST": "t01:VZ:20"}


class Broker:
    env_tag = "s"

    def __init__(self):
        self.positions, self.orders, self.buys = {}, [], []
        self.ask, self.read_ok = 46.0, True

    def positions_snapshot(self):
        return BrokerSnapshot(self.read_ok, dict(self.positions) if self.read_ok else None, "")

    def symbol_orders_snapshot(self, sym):
        return BrokerSnapshot(self.read_ok, [o for o in self.orders if o["symbol"] == sym], "")

    def latest_quote(self, sym):
        return {"bid": self.ask - 0.01, "ask": self.ask, "age_s": 1}

    def submit_bracket_buy(self, sym, qty, stop, tp, client_order_id=None):
        self.buys.append((sym, qty, stop, tp, client_order_id))
        return SimpleNamespace(order_id="b1", status="accepted")


class Prot:
    def __init__(self):
        self.blocked, self.tracks = (False, ""), {}

    def entry_blocker(self):
        return self.blocked


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def make(tmp_path, env=None, **kw):
    b, p, c = Broker(), Prot(), Clock()
    sold = []
    rec = SimpleNamespace(record_trade=lambda **k: sold.append(("trade", k)))
    strat = SimpleNamespace(cfg=SimpleNamespace(risk=SimpleNamespace(stop_loss_pct=10.0, take_profit_pct=20.0)),
                            recorder=rec, exit_manager=None,
                            _do_sell=lambda sym, s, reason, entry_path="": sold.append(("sell", sym, reason)))
    journal = []
    args = dict(env=env if env is not None else dict(STAGING), state_dir=str(tmp_path), account_verified=True,
                universe=["AAPL", "MSFT"], protection=p, lease_ok=lambda: True,
                journal=lambda k, pl: journal.append((k, pl)), market_open=lambda: True, clock=c)
    args.update(kw)
    r = te.TestEntryRunner(b, strat, **args)
    return SimpleNamespace(r=r, b=b, p=p, c=c, sold=sold, journal=journal)


@pytest.mark.parametrize("override,match", [
    ({"env": {"BOT_ENVIRONMENT": "production", "STAGING_TEST_ENTRY_REQUEST": "t:VZ:5"}}, "staging"),
    ({"account_verified": False}, "verified"),
    ({"universe": ["VZ"]}, "universe"),
    ({"protection": None}, "protection"),
])
def test_refuses_to_exist_outside_its_safeguards(tmp_path, override, match):
    with pytest.raises(te.TestEntryRefused, match=match):
        make(tmp_path, **override)


def test_one_share_test_prefix_and_protection_before_hold_then_exit(tmp_path):
    t = make(tmp_path)
    assert t.r.tick() == "entered"
    sym, qty, stop, tp, cid = t.b.buys[0]
    assert (sym, qty, cid) == ("VZ", 1, "sTEST-VZ-t01") and stop == 41.4 and tp == 55.2
    assert t.sold[0][1]["entry_path"] == "TEST" and t.sold[0][1]["reason"].startswith("TEST")
    t.b.positions["VZ"] = {"qty": 1.0}
    assert t.r.tick() == "waiting for confirmed protection"
    t.p.tracks["VZ"] = SimpleNamespace(state="PROTECTED")
    assert t.r.tick() == "protected"
    t.c.t += 10 * 60
    assert t.r.tick() == "holding"
    t.c.t += 11 * 60
    assert t.r.tick() == "exit requested" and t.sold[-1] == ("sell", "VZ", "TEST exit t01")
    t.b.positions.clear()
    assert t.r.tick() == "closed"
    assert all(pl["test"] for _, pl in t.journal)


def test_request_used_once_even_across_restarts(tmp_path):
    t = make(tmp_path)
    t.r.tick()
    t.b.positions.clear()
    t.r.state["active"] = None
    t.r._save()
    t2 = make(tmp_path)                              # restart, same volume
    assert t2.r.tick() == "request already used" and t2.b.buys == []


def test_no_new_entry_until_previous_is_fully_reconciled(tmp_path):
    t = make(tmp_path)
    t.b.positions["VZ"] = {"qty": 1.0}               # leftover test share
    assert t.r.tick().startswith("waiting: previous test position")
    t.b.positions.clear()
    t.b.orders = [{"symbol": "KMI", "status": "held"}]  # leftover working order on a test symbol
    assert t.r.tick().startswith("waiting: previous test position")
    assert t.b.buys == []


@pytest.mark.parametrize("setup,why", [
    (lambda t: setattr(t.p, "blocked", (True, "AAPL protection status UNKNOWN")), "protection gate"),
    (lambda t: setattr(t.b, "ask", 150.0), "price check"),
    (lambda t: setattr(t.b, "read_ok", False), "broker state unknown"),
])
def test_gates(tmp_path, setup, why):
    t = make(tmp_path)
    setup(t)
    assert why in t.r.tick() and t.b.buys == []


def test_lease_and_market_gates(tmp_path):
    t = make(tmp_path, lease_ok=lambda: False)
    assert "lease" in t.r.tick()
    t = make(tmp_path, market_open=lambda: False)
    assert "market closed" in t.r.tick()


def test_kill_switch_env_and_file(tmp_path):
    t = make(tmp_path, env=dict(STAGING, STAGING_TEST_ENTRIES_KILL="true"))
    assert t.r.tick() == "killed"
    t = make(tmp_path)
    open(os.path.join(str(tmp_path), "KILL"), "w").close()
    assert t.r.tick() == "killed" and t.b.buys == []


def test_hard_cap_and_whitelist(tmp_path):
    t = make(tmp_path)
    t.r.entries_this_deployment = te.MAX_ENTRIES_PER_DEPLOYMENT
    assert "hard cap" in t.r.tick()
    t = make(tmp_path, env=dict(STAGING, STAGING_TEST_ENTRY_REQUEST="t9:AAPL:5"))
    assert t.r.tick() == "refused" and t.b.buys == []


def test_rejected_entry_is_not_retried(tmp_path):
    t = make(tmp_path)

    def reject(*a, **k):
        raise OrderRejected("422", 422)
    t.b.submit_bracket_buy = reject
    assert t.r.tick() == "entry failed"
    assert t.r.tick() == "request already used"


def test_production_deployment_ignores_the_flag(cfg_env, tmp_path):
    import main
    from bot.config import load_config

    cfg_env.setenv("STAGING_TEST_ENTRIES_ENABLED", "true")
    cfg_env.setenv("STAGING_TEST_ENTRY_REQUEST", "t01:VZ:5")
    broker, _, _, strategy, _ = main.build(load_config("nonexistent.ini"))
    assert strategy.test_entries is None
