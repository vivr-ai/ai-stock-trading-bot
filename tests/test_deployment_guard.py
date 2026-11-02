"""Stage 1 guards: staging can never start on the wrong (or a live) Alpaca
account, and production behaves exactly as before when nothing is set."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import load_config  # noqa: E402
from bot.deployment_guard import AccountMismatchError, verify_account_identity  # noqa: E402
from bot.notifications.telegram_client import TelegramNotifier  # noqa: E402

BASE = {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}
CLEAR = ["BOT_ENVIRONMENT", "EXPECTED_ALPACA_ACCOUNT_NUMBER", "TRADING_MODE",
         "ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY", "LIVE_TRADING_CONFIRMED"]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no config.ini picked up
    for k in CLEAR:
        monkeypatch.delenv(k, raising=False)
    for k, v in BASE.items():
        monkeypatch.setenv(k, v)
    return monkeypatch


class _Broker:
    def __init__(self, number="PA123456789", fail=False):
        self.number, self.fail = number, fail

    def account_identity(self):
        if self.fail:
            raise ConnectionError("alpaca down")
        return {"account_number": self.number, "paper": True}


def test_production_default_unchanged(env):
    cfg = load_config("nonexistent.ini")
    assert cfg.deployment.environment == "production"
    assert cfg.deployment.expected_account_number == ""
    # no expectation set -> never raises, even if Alpaca is unreachable
    verify_account_identity(cfg, _Broker())
    verify_account_identity(cfg, _Broker(fail=True))


def test_staging_requires_expected_account(env):
    env.setenv("BOT_ENVIRONMENT", "staging")
    with pytest.raises(ValueError, match="EXPECTED_ALPACA_ACCOUNT_NUMBER"):
        load_config("nonexistent.ini")


def test_staging_rejects_live_keys_and_non_paper_mode(env):
    env.setenv("BOT_ENVIRONMENT", "staging")
    env.setenv("EXPECTED_ALPACA_ACCOUNT_NUMBER", "PA1")
    env.setenv("ALPACA_LIVE_API_KEY", "live")
    with pytest.raises(ValueError, match="must NOT be set in staging"):
        load_config("nonexistent.ini")
    env.delenv("ALPACA_LIVE_API_KEY")
    env.setenv("TRADING_MODE", "dry_run")
    with pytest.raises(ValueError):
        load_config("nonexistent.ini")


def test_unknown_environment_rejected(env):
    env.setenv("BOT_ENVIRONMENT", "prod")
    with pytest.raises(ValueError, match="BOT_ENVIRONMENT"):
        load_config("nonexistent.ini")


def test_matching_account_starts(env):
    env.setenv("BOT_ENVIRONMENT", "staging")
    env.setenv("EXPECTED_ALPACA_ACCOUNT_NUMBER", "PA123456789")
    cfg = load_config("nonexistent.ini")
    assert verify_account_identity(cfg, _Broker("PA123456789")).endswith("6789")


def test_mismatched_account_refuses_to_start(env):
    env.setenv("BOT_ENVIRONMENT", "staging")
    env.setenv("EXPECTED_ALPACA_ACCOUNT_NUMBER", "PA999")
    cfg = load_config("nonexistent.ini")
    with pytest.raises(AccountMismatchError):
        verify_account_identity(cfg, _Broker("PA123456789"))


def test_unverifiable_account_fails_closed_when_expected(env):
    env.setenv("EXPECTED_ALPACA_ACCOUNT_NUMBER", "PA123456789")
    cfg = load_config("nonexistent.ini")
    with pytest.raises(AccountMismatchError):
        verify_account_identity(cfg, _Broker(fail=True))


def test_error_message_never_prints_full_account_number(env):
    env.setenv("EXPECTED_ALPACA_ACCOUNT_NUMBER", "PA999999999")
    cfg = load_config("nonexistent.ini")
    with pytest.raises(AccountMismatchError) as e:
        verify_account_identity(cfg, _Broker("PA123456789"))
    assert "PA123456789" not in str(e.value) and "PA999999999" not in str(e.value)


def test_telegram_label_prefixes_messages(monkeypatch):
    sent = {}

    class _Resp:
        status_code = 200
        text = "ok"

        def json(self):
            return {"ok": True}

    import requests
    monkeypatch.setattr(requests, "post", lambda url, json=None, timeout=None, **k: sent.update(json) or _Resp())
    t = TelegramNotifier("tok", "chat", label="[STAGING]")
    t.send("hello", severity="critical")
    assert "[STAGING] hello" in sent["text"]
