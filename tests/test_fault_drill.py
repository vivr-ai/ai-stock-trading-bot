"""Stage 6c drill runner: refuses outside staging; invariant checker counts
breaches from Alpaca's own order data."""
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_s = importlib.util.spec_from_file_location("fault_drill", ROOT / "scripts" / "fault_drill.py")
fd = importlib.util.module_from_spec(_s)
sys.modules[_s.name] = fd
_s.loader.exec_module(fd)


class Api:
    def __init__(self, pos, orders):
        self.pos, self.orders, self.base = pos, orders, fd.PAPER

    def call(self, method, path, body=None, params=None, timeout=15):
        if path.startswith("/v2/positions/"):
            return (200, {"qty": str(self.pos)}) if self.pos else (404, {})
        if path == "/v2/orders":
            return 200, self.orders
        if path == "/v2/account":
            return 200, {"account_number": "PA0000OTHER"}
        return 200, None


def o(cid, status, typ="limit", filled=0, at="2026-11-12T15:00:01Z"):
    return {"client_order_id": cid, "side": "sell", "type": typ, "status": status, "filled_qty": str(filled),
            "filled_at": at if filled else None, "created_at": at}


def test_invariants_pass_and_breach():
    seen = {"since": "2026-11-12T15:00:00Z"}
    fd.invariant_check(Api(1, [o("sR-1", "new"), o("stop", "held", "stop")]), 1.0, seen)
    assert "breaches" not in seen and seen["max_live"] == 1
    fd.invariant_check(Api(0, [o("sR-1", "new"), o("sX-1", "new")]), 1.0, seen)
    assert seen["breaches"] == ["2 working sells"]
    seen = {"since": "2026-11-12T15:00:00Z"}
    fd.invariant_check(Api(0, [o("sR-1", "filled", filled=1), o("sX-1", "filled", "market", filled=1)]), 1.0, seen)
    assert seen["breaches"] == ["sold 2.0 > bought 1.0"]


def test_guard_refuses_wrong_environment_and_wrong_account():
    with pytest.raises(SystemExit, match="staging"):
        fd.guard({"BOT_ENVIRONMENT": "production"}, Api(0, []))
    with pytest.raises(SystemExit, match="identity"):
        fd.guard({"BOT_ENVIRONMENT": "staging", "EXPECTED_ALPACA_ACCOUNT_NUMBER": "PA0000N70Z"}, Api(0, []))


def test_replacement_chain_counts_as_one_sell():
    seen = {"since": "2026-11-12T15:00:00Z"}
    old = dict(o("tp", "pending_replace"), id="a")
    new = dict(o("sR-1", "pending_new"), id="b", replaces="a")
    fd.invariant_check(Api(1, [old, new]), 1.0, seen)
    assert "breaches" not in seen and seen["max_live"] == 1
