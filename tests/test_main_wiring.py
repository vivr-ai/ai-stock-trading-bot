"""Start-up wiring smoke test: main.build() attaches the Stage 1 account guard
and the Stage 2 gate/journal, with a fake broker (no network)."""
import os
import sys

import psycopg2
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from bot.config import load_config  # noqa: E402
from bot.deployment_guard import AccountMismatchError  # noqa: E402
from tests.pg_fixture import pg_url  # noqa: E402,F401


class _FakeBroker:
    def __init__(self, *a, **k):
        self.connects_to_paper = True

    def account_identity(self):
        return {"account_number": "PA111111111", "paper": True}


@pytest.fixture
def cfg_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in ["BOT_ENVIRONMENT", "EXPECTED_ALPACA_ACCOUNT_NUMBER", "DATABASE_URL",
              "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setenv("NEWS_PROVIDER", "newsapi")      # avoid constructing alpaca news client
    monkeypatch.setenv("NEWSAPI_API_KEY", "x")
    monkeypatch.setattr(main, "AlpacaBroker", _FakeBroker)
    return monkeypatch


def test_build_wires_gate_and_journal(cfg_env, tmp_path):
    cfg = load_config("nonexistent.ini")
    broker, _, _, strategy, recorder = main.build(cfg)
    assert strategy.db_gate is not None
    assert strategy.db_gate.enabled is False            # no DATABASE_URL -> no-op, behaviour unchanged
    assert recorder._recorder.journal is not None


def test_build_refuses_wrong_account_in_staging(cfg_env):
    cfg_env.setenv("BOT_ENVIRONMENT", "staging")
    cfg_env.setenv("EXPECTED_ALPACA_ACCOUNT_NUMBER", "PA999999999")
    cfg = load_config("nonexistent.ini")
    with pytest.raises(AccountMismatchError):
        main.build(cfg)


def test_v3_staging_startup_smoke(cfg_env, tmp_path, pg_url):
    cfg_env.setenv("BOT_ENVIRONMENT", "staging")
    cfg_env.setenv("EXPECTED_ALPACA_ACCOUNT_NUMBER", "PA111111111")
    cfg_env.setenv("DATABASE_URL", pg_url)
    cfg_env.setenv("JOURNAL_DIR", str(tmp_path / "data" / "journal"))
    cfg_env.setenv("INSTANCE_LEASE_ENABLED", "true")
    cfg_env.setenv("PROTECTION_SWEEP_ENABLED", "true")
    cfg_env.setenv("STRATEGY_SENTIMENT_EXIT_MODE", "shadow")
    cfg_env.setenv("RISK_TRAILING_V2_ENABLED", "true")
    cfg = load_config("nonexistent.ini")
    broker, _, _, strategy, recorder = main.build(cfg)
    assert strategy.lease is not None and broker.order_guard is not None
    assert strategy.protection is not None and len(strategy.entry_blockers) == 2
    assert strategy.journal.directory.endswith("data/journal")
    assert strategy.lease.name == "trader:*******1111"
    assert strategy.lease.try_acquire()                 # real SQL on the test DB
    assert broker.order_guard("exit")[0] is False      # not reconciled yet: nothing goes out
    strategy.lease.release()


def test_exit_manager_requires_lease(cfg_env):
    cfg_env.setenv("EXIT_MANAGER_ENABLED", "true")
    with pytest.raises(ValueError, match="INSTANCE_LEASE_ENABLED"):
        load_config("nonexistent.ini")


def test_protection_hook_reconciles_the_lease_off_hours(cfg_env, tmp_path, pg_url):
    """10 Nov: after an off-hours redeploy the lease stayed un-reconciled
    until 09:00 New York, so no protection sweep ran overnight. The
    protection manager's 'active' hook now runs the lease's ensure_ready."""
    cfg_env.setenv("BOT_ENVIRONMENT", "staging")
    cfg_env.setenv("EXPECTED_ALPACA_ACCOUNT_NUMBER", "PA111111111")
    cfg_env.setenv("DATABASE_URL", pg_url)
    cfg_env.setenv("JOURNAL_DIR", str(tmp_path / "journal"))
    cfg_env.setenv("INSTANCE_LEASE_ENABLED", "true")
    cfg_env.setenv("PROTECTION_SWEEP_ENABLED", "true")
    broker, _, _, strategy, _ = main.build(load_config("nonexistent.ini"))
    with psycopg2.connect(pg_url) as c, c.cursor() as cur:
        cur.execute("DELETE FROM bot_lease WHERE name = %s", (strategy.lease.name,))
    calls = []
    strategy.lease.reconcile = lambda: (calls.append(1) or True, "0 positions held")
    assert strategy.lease.try_acquire() and strategy.lease.state == "reconciling"
    assert strategy.protection.active() is True          # reconciled here, no cycle needed
    assert calls == [1] and strategy.lease.state == "held"
    assert strategy.protection.active() is True and calls == [1]   # cheap once held
    strategy.lease.release()


@pytest.mark.parametrize("enabled", [False, True])
def test_exit_manager_wiring_follows_the_flag(cfg_env, tmp_path, pg_url, enabled):
    cfg_env.setenv("BOT_ENVIRONMENT", "staging")
    cfg_env.setenv("EXPECTED_ALPACA_ACCOUNT_NUMBER", "PA111111111")
    cfg_env.setenv("DATABASE_URL", pg_url)
    cfg_env.setenv("JOURNAL_DIR", str(tmp_path / "journal"))
    cfg_env.setenv("INSTANCE_LEASE_ENABLED", "true")
    cfg_env.setenv("PROTECTION_SWEEP_ENABLED", "true")
    cfg_env.setenv("EXIT_MANAGER_ENABLED", "true" if enabled else "false")
    broker, _, _, strategy, _ = main.build(load_config("nonexistent.ini"))
    if not enabled:
        assert strategy.exit_manager is None                   # today's close_position path
        assert getattr(strategy.protection, "hold_off", None) is None
        return
    xm = strategy.exit_manager
    assert xm is not None and xm.env_tag == "s" and xm.lock is strategy.order_lock
    assert strategy.protection.hold_off == xm.holds_protection
    assert xm.lease_ok() is False                               # lease not held: D8, nothing sent
