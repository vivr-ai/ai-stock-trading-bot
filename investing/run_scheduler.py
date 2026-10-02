#!/usr/bin/env python3
"""Entrypoint for the investment-tracker Railway service: runs the daily
scan on a schedule, forever, as its own small persistent worker process.

Why a persistent worker + APScheduler instead of Railway's native Cron
Schedule (a run-once-and-exit trigger): Railway's cron trigger fires at a
fixed UTC time, which would drift an hour twice a year across the AEST/AEDT
daylight-saving switch unless manually adjusted. APScheduler's CronTrigger
with timezone="Australia/Sydney" tracks the ASX open correctly through DST
automatically - this is the exact technique bot/scheduler.py already uses
in this repo for the trading bot's own US-market-hours schedule, just
pointed at a different timezone and a once-daily job instead of an
intraday one. Reusing a proven pattern, not importing it (no shared code
with bot/).

Resilience, matching bot/scheduler.py: owns SIGTERM/SIGINT (Railway sends
SIGTERM on redeploy/restart) for a clean shutdown, and restarts itself with
backoff if the scheduler loop dies unexpectedly rather than letting the
whole process exit and need a human to notice.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from investing.config import load_config  # noqa: E402
from investing.scan import run_scan  # noqa: E402

logger = logging.getLogger("investing.run_scheduler")


def _run_once() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    cfg = load_config()
    tz = cfg.market_timezone

    def _job() -> None:
        try:
            run_scan(cfg)
        except Exception:  # noqa: BLE001 - one bad run must never kill the scheduler
            logger.exception("Daily scan raised unexpectedly; will retry on the next scheduled run.")

    scheduler = BlockingScheduler(timezone=tz)
    scheduler.add_job(
        _job,
        CronTrigger(day_of_week="mon-fri", hour=cfg.scan_hour, minute=cfg.scan_minute, timezone=tz),
        id="daily_scan", max_instances=1, misfire_grace_time=1800,
    )

    def _handle_stop(signum, _frame):
        logger.info("Received signal %s; stopping scheduler.", signum)
        scheduler.shutdown(wait=False)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle_stop)
        except (ValueError, OSError):
            pass  # not the main thread - best effort only

    logger.info(
        "Investment tracker scheduler started: weekdays %02d:%02d %s. Ctrl+C/SIGTERM to stop.",
        cfg.scan_hour, cfg.scan_minute, tz,
    )
    scheduler.start()  # blocks until shutdown() is called or an exception escapes


def main() -> None:
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        stream=sys.stdout,
    )

    backoff = 10
    while True:
        try:
            _run_once()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Scheduler stopped.")
            return
        except Exception as exc:  # noqa: BLE001 - must never take the whole process down
            logger.exception("Scheduler crashed unexpectedly: %s", exc)
            logger.warning("Restarting scheduler in %ds.", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
            continue
        return  # scheduler.start() returned normally (we called shutdown()) - done


if __name__ == "__main__":
    main()
