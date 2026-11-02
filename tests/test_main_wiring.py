"""Start-up wiring smoke test: main.build() attaches the Stage 1 account guard
and the Stage 2 gate/journal, with a fake broker (no network)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from bot.config import load_config  # noqa: E402
from bot.deployment_guard import AccountMismatchError  # noqa: E402


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
