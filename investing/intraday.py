"""Stage 4/5 (v2): intraday polling of *today's watch list only* - the small
set of holdings that already triggered stage 1/2/3 in the day's scan
(scan.py calls set_todays_watchlist() once that's known). Everything else
in the portfolio is left alone until tomorrow's scan - this module never
touches holdings outside today's list, which keeps the extra Yahoo request
volume v2 adds proportional to how many names are actually in play, not the
whole portfolio.

State lives in-process (module-level globals), not a file/DB - this only
works because the daily-scan job and the intraday-check job run inside the
same long-lived worker (run_scheduler.py), sharing one Python process. A
restart loses today's watch list, which just means intraday checks and the
EOD nudge sit idle until the next scan sets it again - acceptable, since a
restart is rare and the daily scan is the real source of truth anyway.

Two triggers, OR'd, per holding per check:
  - Stage 4a: RSI(14) < 35 (oversold), computed from ~1y of daily closes
    plus the current intraday price as the latest (still-forming) bar.
  - Stage 4b: a NEW intraday low that's at least 0.3% below the last low
    already flagged today for this symbol (so a single soft dip doesn't
    alert over and over - only a genuinely lower low does).
Stage 5 (volume pace >= 1.7x the 20-day average) is a caution *annotation*
on top of a 4a/4b alert, not a trigger of its own - high volume alone isn't
actionable, it's context for an alert that's already firing for another
reason.

Each holding is pinged (Telegram) at most... well, not literally once - a
new, lower low later in the day will alert again - but routine unchanged
conditions won't re-alert every 30 minutes. run_end_of_day_nudge() then
catches anything that made today's watch list but never got an intraday
alert, as a last call before the close.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .config import Config, Holding, load_config
from .indicators import Evaluation, average_volume, compute_rsi
from .telegram_client import TelegramNotifier
from .yahoo_client import fetch_history

logger = logging.getLogger("investing.intraday")

# ---- Locked thresholds (see design doc) ----
RSI_PERIOD = 14
RSI_OVERSOLD = 35.0
DAY_LOW_GAP = 0.003   # 0.3%: how much lower a "new" low must be to re-alert
VOLUME_WINDOW = 20
VOLUME_RATIO = 1.7

# Same courtesy delay as scan.py - the intraday job only ever touches the
# (small) watch list, but it's still extra requests on top of the daily
# scan, so keep being gentle with Yahoo's unofficial endpoint.
INTER_REQUEST_DELAY_SECONDS = 1.5


@dataclass
class WatchItem:
    holding: Holding
    daily_reasons: List[str]
    # The lowest day_low seen so far today that's actually been alerted on.
    # None until the first intraday check observes a day_low at all.
    last_flagged_low: Optional[float] = None
    # Whether this symbol has had at least one intraday (stage 4) alert
    # today - drives the end-of-day nudge, which only chases up items that
    # never got one.
    pinged: bool = False


@dataclass
class TodayState:
    date: str  # YYYY-MM-DD (UTC), for logging/staleness only - not compared
    items: Dict[str, WatchItem] = field(default_factory=dict)


# Module-level, in-process "today" state - see the module docstring for why
# this is safe (single persistent worker) and what a restart costs.
_state: Optional[TodayState] = None


def set_todays_watchlist(evaluations: List[Evaluation]) -> None:
    """Called by scan.py right after the daily stage 1-3 scan, with the
    *triggered* evaluations only. Always replaces the previous state - even
    with an empty list - so yesterday's watch list never leaks into today
    (e.g. if nothing triggers today, intraday checks and the EOD nudge
    should both see an empty list, not stale names from yesterday).
    """
    global _state
    today = datetime.now(timezone.utc).date().isoformat()
    items = {
        ev.holding.symbol: WatchItem(holding=ev.holding, daily_reasons=list(ev.reasons))
        for ev in evaluations
    }
    _state = TodayState(date=today, items=items)
    if items:
        logger.info("Today's watch list set (%s): %s", today, ", ".join(items.keys()))
    else:
        logger.info("Today's watch list set (%s): empty - no holdings triggered stage 1-3.", today)


def _format_intraday_line(item: WatchItem, price: float, reasons: List[str], caution: Optional[str]) -> str:
    text = f"*{item.holding.symbol}* @ {price:g} — " + "; ".join(reasons)
    if caution:
        text += f" (⚠ {caution})"
    return text


def run_intraday_check(cfg: Optional[Config] = None) -> None:
    """Poll every symbol on today's watch list once; send a single
    consolidated Telegram message for whatever triggers this round (or
    nothing, if none do - this runs every 30 minutes during market hours,
    so routine no-ops should stay quiet)."""
    cfg = cfg or load_config()

    if not _state or not _state.items:
        logger.info("Intraday check: today's watch list is empty - nothing to do.")
        return

    alerts: List[str] = []
    for symbol, item in _state.items.items():
        history = fetch_history(symbol)
        time.sleep(INTER_REQUEST_DELAY_SECONDS)
        if history is None:
            logger.warning("Intraday check: could not fetch %s this round - skipping.", symbol)
            continue

        reasons: List[str] = []

        # ---- Stage 4a: RSI(14), using today's current price as the
        # latest (still-forming) bar on top of ~1y of daily closes. ----
        closes_with_today = history.closes + [history.current_price]
        rsi = compute_rsi(closes_with_today, period=RSI_PERIOD)
        if rsi is not None and rsi < RSI_OVERSOLD:
            reasons.append(f"RSI {rsi:.1f} (oversold, <{RSI_OVERSOLD:g})")

        # ---- Stage 4b: new day-low, >= DAY_LOW_GAP below the last low
        # already flagged today. The very first observation of the day
        # just seeds last_flagged_low - it never alerts by itself. ----
        day_low = history.day_low
        if day_low is not None:
            if item.last_flagged_low is not None and day_low <= item.last_flagged_low * (1 - DAY_LOW_GAP):
                reasons.append(
                    f"new day low {day_low:g} ({DAY_LOW_GAP * 100:.1f}%+ below last flagged "
                    f"{item.last_flagged_low:g})"
                )
            if item.last_flagged_low is None or day_low < item.last_flagged_low:
                item.last_flagged_low = day_low
        else:
            logger.debug("Intraday check: %s has no day_low from Yahoo this round (outside market hours?).", symbol)

        # ---- Stage 5: volume-pace caution annotation (not a trigger on
        # its own - only attached when 4a/4b already fired). ----
        caution: Optional[str] = None
        if reasons:
            avg_vol = average_volume(history.volumes, window=VOLUME_WINDOW, exclude_last=True)
            if avg_vol and history.current_volume is not None and avg_vol > 0:
                ratio = history.current_volume / avg_vol
                if ratio >= VOLUME_RATIO:
                    caution = f"volume pace {ratio:.1f}x 20-day average"

        logger.info(
            "%-10s price=%.4f rsi=%s day_low=%s last_flagged_low=%s reasons=%s",
            symbol, history.current_price,
            f"{rsi:.1f}" if rsi is not None else "n/a",
            f"{day_low:g}" if day_low is not None else "n/a",
            f"{item.last_flagged_low:g}" if item.last_flagged_low is not None else "n/a",
            reasons,
        )

        if reasons:
            item.pinged = True
            alerts.append(_format_intraday_line(item, history.current_price, reasons, caution))

    if not alerts:
        logger.info("Intraday check: no triggers this round.")
        return

    message = "\n".join(["⚡ *Intraday alert*"] + alerts)
    notifier = TelegramNotifier(cfg.telegram_bot_token, cfg.telegram_chat_id)
    sent = notifier.send(message)
    logger.info("Intraday check: %d alert(s) this round, Telegram sent=%s.\n%s", len(alerts), sent, message)


def run_end_of_day_nudge(cfg: Optional[Config] = None) -> None:
    """Once per day, shortly before close: a last-call reminder for any
    symbol that made today's watch list but never got an intraday alert
    (so it's easy to miss if you've only been watching for Telegram
    pings)."""
    cfg = cfg or load_config()

    if not _state or not _state.items:
        logger.info("EOD nudge: today's watch list is empty - nothing to do.")
        return

    unpinged = [item for item in _state.items.values() if not item.pinged]
    if not unpinged:
        logger.info("EOD nudge: every watch-list item already had an intraday alert today - skipping.")
        return

    lines = ["⏰ *End-of-day reminder* — still on today's watch list, no intraday alert yet:"]
    for item in unpinged:
        reason_text = "; ".join(item.daily_reasons)
        lines.append(f"*{item.holding.symbol}* — {reason_text}")
    message = "\n".join(lines)

    notifier = TelegramNotifier(cfg.telegram_bot_token, cfg.telegram_chat_id)
    sent = notifier.send(message)
    logger.info("EOD nudge: %d unpinged item(s), Telegram sent=%s.\n%s", len(unpinged), sent, message)
