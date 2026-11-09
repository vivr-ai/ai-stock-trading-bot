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

    def __init__(self, account="PA3XYZN70Z", orders=None, pos=0.0):
        self.account, self.orders, self.pos, self.calls = account, orders or [], pos, []

    def call(self, method, path, body=None, params=None, timeout=15):
        self.calls.append((method, path))
        if path == "/v2/account":
            return 200, {"account_number": self.account}
        if path == "/v2/orders" and method == "GET":
            return 200, [o for o in self.orders if o.get("open", True)]
        if path.startswith("/v2/orders/") and method == "DELETE":
            for o in self.orders:
                if path.endswith(o["id"]):
                    o["open"] = False
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
    must cancel every open order on the (out-of-universe) spike symbol."""
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    api = FakeApi(orders=[{"id": "a", "client_order_id": "spike-x-v1a", "symbol": "VZ"},
                          {"id": "b", "client_order_id": "c7e1-random-leg-id", "symbol": "VZ"},
                          {"id": "c", "client_order_id": "AAPL-slot", "symbol": "AAPL"}], pos=1.0)
    sp.cleanup(api)
    deleted = sorted(p for m, p in api.calls if m == "DELETE" and p.startswith("/v2/orders/"))
    assert deleted == ["/v2/orders/a", "/v2/orders/b"]
    assert api.pos == 0.0


def test_default_symbol_is_outside_the_universe():
    from bot.universe.static_universe import STATIC_UNIVERSE

    assert sp.SYMBOL not in STATIC_UNIVERSE
