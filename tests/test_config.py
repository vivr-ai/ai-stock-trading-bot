"""Confirms the core Railway-crash fix: config loads from env vars alone,
with zero files present, and config.ini (when present) only fills gaps."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from bot.config import load_config


def test_loads_from_env_vars_with_no_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "envkey")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "envsecret")
    monkeypatch.setenv("RISK_STOP_LOSS_PCT", "12.5")

    missing_path = str(tmp_path / "does_not_exist.ini")
    cfg = load_config(missing_path)  # must NOT raise FileNotFoundError

    assert cfg.alpaca.api_key == "envkey"
    assert cfg.alpaca.secret_key == "envsecret"
    assert cfg.risk.stop_loss_pct == 12.5
    assert cfg.config_file_used is None
    # simplified-mode defaults still apply
    assert cfg.universe.provider == "static"
    assert cfg.news.provider == "alpaca"
    assert cfg.sentiment.provider == "lexicon"


def test_missing_required_keys_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    with pytest.raises(ValueError, match="ALPACA_API_KEY"):
        load_config(str(tmp_path / "nope.ini"))


def test_env_var_overrides_config_ini(tmp_path, monkeypatch):
    ini = tmp_path / "config.ini"
    ini.write_text(
        "[alpaca]\napi_key = inikey\nsecret_key = inisecret\n"
        "[risk]\nstop_loss_pct = 8.0\n"
    )
    monkeypatch.setenv("ALPACA_API_KEY", "envkey")
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    monkeypatch.delenv("RISK_STOP_LOSS_PCT", raising=False)

    cfg = load_config(str(ini))
    assert cfg.alpaca.api_key == "envkey"       # env var wins
    assert cfg.alpaca.secret_key == "inisecret"  # falls back to config.ini
    assert cfg.risk.stop_loss_pct == 8.0         # falls back to config.ini
    assert cfg.config_file_used == str(ini)


def test_new_confirmation_filter_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    cfg = load_config(str(tmp_path / "nope.ini"))
    assert cfg.strategy.require_price_above_sma is True
    assert cfg.strategy.sma_period == 20
    assert cfg.strategy.min_volume_ratio == 1.5
    assert cfg.strategy.market_regime_filter_enabled is True
    assert cfg.strategy.market_regime_ma_period == 50
