"""Corrected trailing stop, v2 (Reliability plan v3, Stage 5).

Behind RISK_TRAILING_V2_ENABLED (default OFF). With it off the original
trailing stop in strategy.py runs unchanged.

Rules (approved):
  * Arms once the position's PEAK gain reaches +3% (trailing_stop_activation_pct)
    and STAYS armed, even if the price falls back below +3%.
  * Once armed, the stop level is the higher of
        peak x (1 - 7%)   (trailing_stop_pct retracement from the recorded peak)
        entry price       (breakeven floor)
    so the trailing rule never sets its exit level below the entry price.
  * Exit when the observed price is at or below that level.
  * Entry price = Alpaca's average entry price for the position.
  * The armed flag, arming time and peak are persisted (trailing_state table,
    keyed by symbol + an entry reference so a new position never inherits an
    old position's state) and mirrored in memory for database outages.

Note on gaps: the bot samples prices every 30 minutes, so a price can be
below the breakeven floor by the time it is observed (e.g. +3% one cycle,
-2% the next). The level is never below entry, but the fill can be. This is
reported in the exit reason.

This module is pure: no I/O, no clock.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

EPS = 1e-9


@dataclass
class TrailState:
    entry_ref: str
    entry_price: float
    armed: bool = False
    armed_at: Optional[float] = None
    peak: float = 0.0
    recovered: bool = False     # rebuilt from bars after both stores were lost


@dataclass
class TrailDecision:
    state: TrailState
    exit: bool
    reason: str
    stop_level: Optional[float]
    newly_armed: bool


def entry_reference(avg_entry_price: float) -> str:
    """Identifies the position episode. The bot never adds to a position, so
    Alpaca's average entry price only changes when a new position starts."""
    return f"{float(avg_entry_price):.4f}"


def merge(*states: Optional[TrailState]) -> Optional[TrailState]:
    """Combine copies of the same episode's state (memory, database):
    armed is sticky (any), peak is the highest, earliest arming time."""
    present = [s for s in states if s is not None]
    if not present:
        return None
    base = present[0]
    armed_ats = [s.armed_at for s in present if s.armed_at is not None]
    return TrailState(entry_ref=base.entry_ref, entry_price=base.entry_price,
                      armed=any(s.armed for s in present),
                      armed_at=min(armed_ats) if armed_ats else None,
                      peak=max(s.peak for s in present),
                      recovered=all(s.recovered for s in present))


def evaluate(state: TrailState, price: float, now: float, activation_pct: float = 3.0,
             trail_pct: float = 7.0) -> TrailDecision:
    entry = state.entry_price
    s = TrailState(**vars(state))
    s.peak = max(s.peak or price, price)
    newly = False
    peak_gain = (s.peak - entry) / entry * 100.0
    if not s.armed and peak_gain + EPS >= activation_pct:
        s.armed, s.armed_at, newly = True, now, True
    if not s.armed:
        return TrailDecision(s, False, f"not armed (peak {peak_gain:+.2f}% < +{activation_pct:g}%)", None, False)
    trail_level = s.peak * (1.0 - trail_pct / 100.0)
    stop_level = max(trail_level, entry)
    floor_binding = entry >= trail_level
    if price <= stop_level + EPS:
        gain_now = (price - entry) / entry * 100.0
        basis = ("breakeven floor at entry" if floor_binding
                 else f"{trail_pct:g}% below the peak")
        gap = " (price gapped below the level between checks)" if price < stop_level - 0.005 else ""
        reason = (f"trailing stop v2: armed at +{activation_pct:g}%, peak ${s.peak:.2f}, stop level "
                  f"${stop_level:.2f} ({basis}), now ${price:.2f} ({gain_now:+.1f}% vs entry){gap}")
        return TrailDecision(s, True, reason, stop_level, newly)
    return TrailDecision(s, False, f"armed; stop level ${stop_level:.2f}", stop_level, newly)
