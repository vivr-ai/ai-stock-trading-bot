"""account_snapshot: staging-only, GET-only evidence of positions and working orders."""
import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
_s = importlib.util.spec_from_file_location("account_snapshot", ROOT / "scripts" / "account_snapshot.py")
snap = importlib.util.module_from_spec(_s)
sys.modules[_s.name] = snap
_s.loader.exec_module(snap)


class Api:
    calls = []

    def __init__(self, *a, **k):
        self.base = "https://paper-api.alpaca.markets"

    def call(self, method, path, body=None, params=None, timeout=15):
        Api.calls.append((method, path, params))
        if path == "/v2/account":
            return 200, {"account_number": "PA0000N70Z"}
        if path == "/v2/positions":
            return 200, []
        if path == "/v2/orders" and (params or {}).get("status") == "open":
            return 200, [{"id": "tp", "symbol": "X", "status": "new", "legs": []}]
        if path == "/v2/orders" and (params or {}).get("symbols") == "X":
            return 200, [{"id": "tp", "symbol": "X", "status": "new"},
                         {"id": "st", "symbol": "X", "status": "held", "type": "stop"}]   # hidden held leg
        if path == "/v2/orders":
            return 200, [{"id": "tp", "symbol": "X", "status": "new"}]
        return 404, {}


def test_refuses_outside_staging(monkeypatch, capsys):
    monkeypatch.setenv("BOT_ENVIRONMENT", "production")
    assert snap.main() == 2


def test_get_only_and_finds_hidden_held_legs(monkeypatch, capsys):
    import importlib.util as ilu

    real = ilu.module_from_spec

    def patched(spec):
        m = real(spec)
        orig = spec.loader.exec_module

        def exec_and_patch(mod):
            orig(mod)
            mod.Api = Api
        spec.loader.exec_module = exec_and_patch
        return m
    monkeypatch.setattr(ilu, "module_from_spec", patched)
    monkeypatch.setenv("BOT_ENVIRONMENT", "staging")
    monkeypatch.setenv("EXPECTED_ALPACA_ACCOUNT_NUMBER", "PA0000N70Z")
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setattr(sys, "argv", ["account_snapshot.py"])
    Api.calls = []
    assert snap.main() == 0
    rep = json.loads(capsys.readouterr().out.split("ACCOUNT_SNAPSHOT ", 1)[1])
    assert rep["n_positions"] == 0 and rep["n_working"] == 2          # the held stop leg was found
    assert {m for m, *_ in Api.calls} == {"GET"}
