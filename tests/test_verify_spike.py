"""Stage 6a spike: safety guards and cleanup scope (no network)."""
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("alpaca_verify_spike", ROOT / "scripts" / "alpaca_verify_spike.py")
sp = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sp
_spec.loader.exec_module(sp)


class FakeApi:
    base = sp.PAPER

    def __init__(self, account="PA3XYZN70Z", orders=None, pos=0.0, market_open=True):
        self.account, self.orders, self.pos, self.calls = account, orders or [], pos, []
        self.market_open = market_open

    def call(self, method, path, body=None, params=None, timeout=15):
        self.calls.append((method, path))
        if path == "/v2/account":
            return 200, {"account_number": self.account}
        if path == "/v2/clock":
            return 200, {"is_open": self.market_open}
        if path == "/v2/orders" and method == "GET":
            p = params or {}
            rows = [o for o in self.orders if o["symbol"] in (p.get("symbols") or o["symbol"]).split(",")]
            if p.get("status") == "open":   # Alpaca hides a held leg under a non-open parent
                rows = [o for o in rows if o.get("status", "new") not in ("canceled", "filled")
                        and not o.get("hidden_from_open")]
            return 200, [dict(o) for o in rows]
        if path.startswith("/v2/orders/") and method == "DELETE":
            for o in self.orders:
                if path.endswith(o["id"]):
                    o["status"] = "canceled"
            return 204, None
        if path.startswith("/v2/positions/"):
            if method == "DELETE":
                self.pos = 0.0
                return 200, {}
            return (200, {"qty": str(self.pos)}) if self.pos else (404, {})
        return 404, {}


ENV = {"BOT_ENVIRONMENT": "staging", "EXPECTED_ALPACA_ACCOUNT_NUMBER": "PA3XYZN70Z"}


def test_guard_accepts_verified_staging():
    assert sp.guard(ENV, FakeApi()).endswith("N70Z")


@pytest.mark.parametrize("env,api", [
    ({**ENV, "BOT_ENVIRONMENT": "production"}, FakeApi()),
    ({**ENV, "ALPACA_LIVE_API_KEY": "x"}, FakeApi()),
    ({**ENV, "EXPECTED_ALPACA_ACCOUNT_NUMBER": ""}, FakeApi()),
    (ENV, FakeApi(account="PA_PRODUCTION")),
])
def test_guard_refuses(env, api):
    with pytest.raises(SystemExit):
        sp.guard(env, api)


def test_guard_refuses_live_endpoint():
    api = FakeApi()
    api.base = "https://api.alpaca.markets"
    with pytest.raises(SystemExit):
        sp.guard(ENV, api)


def test_cleanup_cancels_every_order_on_the_spike_symbol_only(monkeypatch):
    """9 Nov run: a replaced take-profit leg has Alpaca's own ID, so cleanup
    must cancel every working order on the (out-of-universe) spike symbol -
    including a held stop leg the open-orders list doesn't show (10 Nov)."""
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    api = FakeApi(orders=[
        {"id": "a", "client_order_id": "spike-x-v1a", "symbol": "VZ", "status": "new"},
        {"id": "b", "client_order_id": "c7e1-random-leg-id", "symbol": "VZ", "status": "new"},
        {"id": "s", "client_order_id": "held-stop", "symbol": "VZ", "status": "held",
         "hidden_from_open": True},
        {"id": "f", "client_order_id": "old-entry", "symbol": "VZ", "status": "filled"},
        {"id": "c", "client_order_id": "AAPL-slot", "symbol": "AAPL", "status": "new"}], pos=1.0)
    sp.cleanup(api)
    deleted = sorted(p for m, p in api.calls if m == "DELETE" and p.startswith("/v2/orders/"))
    assert deleted == ["/v2/orders/a", "/v2/orders/b", "/v2/orders/s"]
    assert api.pos == 0.0


def test_cleanup_leaves_everything_in_place_when_market_closed(monkeypatch):
    """Cancelling outside hours would strip the stop off a share that can't
    be sold until the open."""
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    api = FakeApi(orders=[{"id": "s", "symbol": "VZ", "status": "held", "client_order_id": "x"}],
                  pos=1.0, market_open=False)
    sp.cleanup(api)
    assert not [p for m, p in api.calls if m == "DELETE"] and api.pos == 1.0
    assert any(r["check"] == "cleanup.skipped" for r in sp.RESULTS)


def test_default_symbol_is_outside_the_universe():
    from bot.universe.static_universe import STATIC_UNIVERSE

    assert sp.SYMBOL not in STATIC_UNIVERSE
