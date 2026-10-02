"""Orchestrates one daily scan: fetch each holding + its benchmark, run the
stage 1-3 funnel, and send a single Telegram message listing today's watch
list (or send nothing if the list is empty, to avoid daily noise).

Run directly for a one-off check:  python -m investing.scan
Run on a schedule via run_scheduler.py (the Railway worker entrypoint).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import intraday
from .config import Config, load_config
from .indicators import Evaluation, evaluate
from .telegram_client import TelegramNotifier
from .yahoo_client import PriceHistory, fetch_history

logger = logging.getLogger("investing.scan")

# A pause between distinct symbols - a bit kinder to Yahoo's unofficial
# endpoint than firing requests back-to-back, on top of the crumb/cookie
# handling in yahoo_client.py. Yahoo's rate limiting turned out to be
# IP-level (not just this endpoint being picky about anonymous requests),
# so this alone won't fix an already-throttled IP - it just avoids making
# a healthy one look like a scraper in the first place.
INTER_REQUEST_DELAY_SECONDS = 1.5


def _format_message(evaluations: List[Evaluation]) -> str:
    lines = ["\U0001F4C9 *Today's watch list*"]
    for ev in evaluations:
        reason_text = "; ".join(ev.reasons)
        lines.append(f"*{ev.holding.symbol}* — {reason_text}")
    return "\n".join(lines)


def run_scan(cfg: Optional[Config] = None) -> List[Evaluation]:
    cfg = cfg or load_config()

    if not cfg.holdings:
        logger.warning("No holdings loaded - nothing to scan (check INVEST_HOLDINGS_FILE / holdings.json).")
        return []

    # Fetch each distinct benchmark once, not once per holding.
    benchmark_symbols = {h.benchmark for h in cfg.holdings}
    benchmark_history: Dict[str, Optional[PriceHistory]] = {}
    for sym in benchmark_symbols:
        benchmark_history[sym] = fetch_history(sym)
        if benchmark_history[sym] is None:
            logger.error("Could not fetch benchmark %s - stage 3 will be skipped for holdings using it.", sym)
        time.sleep(INTER_REQUEST_DELAY_SECONDS)

    evaluations: List[Evaluation] = []
    for holding in cfg.holdings:
        history = fetch_history(holding.symbol)
        time.sleep(INTER_REQUEST_DELAY_SECONDS)
        if history is None:
            logger.error("Skipping %s this scan - could not fetch price history.", holding.symbol)
            continue
        ev = evaluate(holding, history, benchmark_history.get(holding.benchmark))
        evaluations.append(ev)
        logger.info(
            "%-10s price=%.4f triggered=%s reasons=%s",
            holding.symbol, ev.current_price, ev.triggered, ev.reasons,
        )

    triggered = [ev for ev in evaluations if ev.triggered]

    # Always set today's watch list - even when empty - so v2's intraday
    # checks and EOD nudge never run against a stale list carried over
    # from a previous day that triggered but today didn't.
    intraday.set_todays_watchlist(triggered)

    if not triggered:
        logger.info("Scan complete at %s: nothing on today's watch list.", datetime.now(timezone.utc).isoformat())
        return evaluations

    message = _format_message(triggered)
    notifier = TelegramNotifier(cfg.telegram_bot_token, cfg.telegram_chat_id)
    sent = notifier.send(message)
    logger.info(
        "Scan complete: %d holding(s) on today's watch list. Telegram sent=%s.\n%s",
        len(triggered), sent, message,
    )
    return evaluations


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
    results = run_scan()
    sys.exit(0)
