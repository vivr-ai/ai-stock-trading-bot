"""Preserves existing behavior: 5% sizing, 10-position cap, duplicate-buy
prevention, exposure cap. Not new logic — a safety net for the refactor."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import dataclass

from bot.trading.risk import RiskManager


@dataclass
class _RiskCfg:
    dry_run: bool = True
    max_position_pct: float = 5.0
    max_open_positions: int = 10
    max_total_exposure_pct: float = 50.0
    max_new_positions_per_cycle: int = 3
    max_positions_per_sector: int = 3
    reentry_cooldown_hours: float = 24.0
    stop_loss_pct: float = 10.0
    take_profit_pct: float = 20.0
    max_order_notional: float = 0.0
    daily_loss_limit_pct: float = 4.0


def test_position_sizing_is_five_percent():
    risk = RiskManager(_RiskCfg())
    decision = risk.evaluate("AAPL", price=100.0, portfolio_value=100_000,
                             buying_power=100_000, current_exposure=0, open_positions={})
    assert decision.approved
    assert decision.plan.notional <= 5_000.0
    assert decision.plan.stop_price == 90.0
    assert decision.plan.take_profit_price == 120.0


def test_blocks_duplicate_buy():
    risk = RiskManager(_RiskCfg())
    decision = risk.evaluate("AAPL", price=100.0, portfolio_value=100_000,
                             buying_power=100_000, current_exposure=0,
                             open_positions={"AAPL": 10})
    assert not decision.approved
    assert "already holding" in decision.reason


def test_blocks_at_max_open_positions():
    risk = RiskManager(_RiskCfg())
    positions = {f"SYM{i}": 1 for i in range(10)}
    decision = risk.evaluate("AAPL", price=100.0, portfolio_value=100_000,
                             buying_power=100_000, current_exposure=0, open_positions=positions)
    assert not decision.approved
    assert "max_open_positions" in decision.reason
