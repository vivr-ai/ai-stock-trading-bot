"""Stage 1-3 of the locked indicator funnel (see the ClaudeTrader project's
'Investment Portfolio Tracker' design doc). v1 only - stage 4/5 (intraday
RSI, day-low tracking, volume flag) are v2 and not implemented here.

Stages 1, 2, and 3 are combined with OR: any one is enough to put a holding
on today's watch list. Stage 2's two trend checks (50-day MA, 52-week high)
are themselves OR'd together. This is deliberate - stage 1 (cost basis)
alone would never fire again for a long-term winner whose price has run
far above its cost basis, so stages 2/3 exist specifically to keep catching
a genuine pullback for names like that.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .config import Holding
from .yahoo_client import PriceHistory

# ---- Locked thresholds (see design doc for the reasoning behind each) ----
COST_BASIS_BUFFER = 1.03        # stage 1: price <= ac_share * 1.03
MA50_PULLBACK = 0.06            # stage 2a: price >= 6% below the 50-day MA
HIGH52W_PULLBACK = 0.12         # stage 2b: price >= 12% off the 52-week high
RELATIVE_UNDERPERFORM_PP = 0.05  # stage 3: stock underperforms index by >= 5pp (1mo)

MA_WINDOW = 50
MONTH_TRADING_DAYS = 21  # ~1 trading month


@dataclass
class Evaluation:
    holding: Holding
    current_price: float
    triggered: bool
    reasons: List[str]
    # Context always computed (even if not triggering) so logs/debugging are
    # useful, and so a future v2 can reuse these without re-fetching.
    pct_vs_cost_basis: Optional[float] = None
    pct_vs_ma50: Optional[float] = None
    pct_off_52w_high: Optional[float] = None
    relative_1m_underperformance_pp: Optional[float] = None


def _pct_change(now: float, then: float) -> Optional[float]:
    if then == 0:
        return None
    return (now / then) - 1.0


def _moving_average(closes: List[float], window: int) -> Optional[float]:
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def _one_month_return(closes: List[float]) -> Optional[float]:
    if len(closes) <= MONTH_TRADING_DAYS:
        return None
    return _pct_change(closes[-1], closes[-1 - MONTH_TRADING_DAYS])


def evaluate(holding: Holding, history: PriceHistory, benchmark_history: Optional[PriceHistory]) -> Evaluation:
    price = history.current_price
    reasons: List[str] = []

    # ---- Stage 1: cost basis ----
    pct_vs_cost_basis = _pct_change(price, holding.ac_share) if holding.ac_share else None
    stage1 = holding.ac_share > 0 and price <= holding.ac_share * COST_BASIS_BUFFER
    if stage1:
        if price <= holding.ac_share:
            reasons.append(f"at or below cost basis (AC/share {holding.ac_share:g})")
        else:
            reasons.append(f"within 3% of cost basis (AC/share {holding.ac_share:g})")

    # ---- Stage 2: trend position (OR of two checks) ----
    ma50 = _moving_average(history.closes, MA_WINDOW)
    pct_vs_ma50 = _pct_change(price, ma50) if ma50 else None
    stage2a = ma50 is not None and price <= ma50 * (1 - MA50_PULLBACK)
    if stage2a:
        reasons.append(f"{abs(pct_vs_ma50) * 100:.1f}% below its 50-day average")

    week52_high = max(history.closes) if history.closes else None
    pct_off_52w_high = _pct_change(price, week52_high) if week52_high else None
    stage2b = week52_high is not None and price <= week52_high * (1 - HIGH52W_PULLBACK)
    if stage2b:
        reasons.append(f"{abs(pct_off_52w_high) * 100:.1f}% off its 52-week high")

    # ---- Stage 3: relative-to-index performance ----
    relative_underperformance_pp = None
    stage3 = False
    if benchmark_history is not None:
        stock_1m = _one_month_return(history.closes)
        index_1m = _one_month_return(benchmark_history.closes)
        if stock_1m is not None and index_1m is not None:
            relative_underperformance_pp = index_1m - stock_1m
            stage3 = relative_underperformance_pp >= RELATIVE_UNDERPERFORM_PP
            if stage3:
                reasons.append(
                    f"underperforming {holding.benchmark} by "
                    f"{relative_underperformance_pp * 100:.1f}pp this month"
                )

    triggered = stage1 or stage2a or stage2b or stage3
    return Evaluation(
        holding=holding,
        current_price=price,
        triggered=triggered,
        reasons=reasons,
        pct_vs_cost_basis=pct_vs_cost_basis,
        pct_vs_ma50=pct_vs_ma50,
        pct_off_52w_high=pct_off_52w_high,
        relative_1m_underperformance_pp=relative_underperformance_pp,
    )
