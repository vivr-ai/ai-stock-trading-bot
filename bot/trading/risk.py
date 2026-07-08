"""Risk management: position sizing and pre-trade gating.

Enforces the spec's risk rules in one auditable place:
  * max 5% of portfolio value per position
  * max 10 open positions
  * max 50% of total capital deployed across all positions
  * (optional) hard per-order dollar cap and daily-loss kill switch
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class OrderPlan:
    symbol: str
    qty: int
    price: float
    notional: float
    stop_price: float
    take_profit_price: float


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    plan: Optional[OrderPlan] = None


class RiskManager:
    def __init__(self, risk_cfg):
        self.cfg = risk_cfg

    def daily_loss_breached(self, equity: float, start_of_day_equity: float) -> bool:
        if self.cfg.daily_loss_limit_pct <= 0 or start_of_day_equity <= 0:
            return False
        drawdown = (start_of_day_equity - equity) / start_of_day_equity * 100.0
        if drawdown >= self.cfg.daily_loss_limit_pct:
            logger.warning("Daily loss limit hit: drawdown %.2f%% >= %.2f%%",
                           drawdown, self.cfg.daily_loss_limit_pct)
            return True
        return False

    def evaluate(
        self,
        symbol: str,
        price: float,
        portfolio_value: float,
        buying_power: float,
        current_exposure: float,
        open_positions: Dict[str, float],
    ) -> RiskDecision:
        if price <= 0:
            return RiskDecision(False, "no valid price")

        # Rule: no current position already exists.
        if symbol in open_positions and open_positions[symbol] != 0:
            return RiskDecision(False, "already holding this symbol")

        # Rule: never more than 10 open positions.
        if len(open_positions) >= self.cfg.max_open_positions:
            return RiskDecision(False, f"max_open_positions reached ({self.cfg.max_open_positions})")

        # Position sizing: 5% of portfolio value, optionally capped by a hard
        # per-order dollar limit, and never above available buying power.
        target_notional = portfolio_value * (self.cfg.max_position_pct / 100.0)
        if self.cfg.max_order_notional > 0:
            target_notional = min(target_notional, self.cfg.max_order_notional)
        target_notional = min(target_notional, buying_power)

        # Rule: never more than 50% of capital deployed in total.
        exposure_cap = portfolio_value * (self.cfg.max_total_exposure_pct / 100.0)
        room = exposure_cap - current_exposure
        if room <= 0:
            return RiskDecision(
                False,
                f"total exposure cap reached "
                f"({current_exposure:.0f}/{exposure_cap:.0f})",
            )
        target_notional = min(target_notional, room)

        qty = int(math.floor(target_notional / price))
        if qty < 1:
            return RiskDecision(False, "position size rounds to 0 shares")

        notional = qty * price
        return RiskDecision(
            True,
            "approved",
            OrderPlan(
                symbol=symbol,
                qty=qty,
                price=price,
                notional=round(notional, 2),
                stop_price=round(price * (1.0 - self.cfg.stop_loss_pct / 100.0), 2),
                take_profit_price=round(price * (1.0 + self.cfg.take_profit_pct / 100.0), 2),
            ),
        )
