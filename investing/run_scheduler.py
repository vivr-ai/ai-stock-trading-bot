#!/usr/bin/env python3
"""Entrypoint for the investment-tracker Railway service: runs the daily
scan, the intraday watch-list checks, and the end-of-day nudge, forever, as
one small persistent worker process.

Why a persistent worker + APScheduler instead of Railway's native Cron
Schedule (a run-once-and-exit trigger): Railway's cron trigger fires at a
fixed UTC time, which would drift an hour twice a year across the AEST/AEDT
daylight-saving switch unless manually adjusted. APScheduler's CronTrigger
with timezone="Australia/Sydney" tracks the ASX open correctly through DST
automatically - this is the exact technique bot/scheduler.py already uses
in this repo for the trading bot's own US-market-hours schedule, just
pointed at a different timezone and cadence. Reusing a proven pattern, not
importing it (no shared code with bot/).

A single persistent process is also *why* v2 (intraday.py) can share
today's watch list between jobs with nothing more than a module-level
variable - the daily scan job and the intraday-check job are two cron
triggers in the same process, not two separate services.

Jobs (all weekdays, in INVEST_MARKET_TIMEZONE - default Australia/Sydney):
  - daily_scan: once, near market open (default 10:15) - stage 1-3,
    decides today's watch list.
  - intraday_check: every 30 min, 10:00-15:30 - stage 4/5 against whatever
    is on today's watch list (a no-op most rounds, on a quiet day).
  - eod_nudge: once, 15:15 - a last-call reminder for watch-list items that
    never got an intraday alert (ASX closes 16:00, so this is 45 min out).

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
from investing.intraday import run_end_of_day_nudge, run_intraday_check  # noqa: E402
from investing.scan import run_scan  # noqa: E402

logger = logging.getLogger("investing.run_scheduler")

# Intraday/EOD cadence is locked by design (see design doc), not exposed as
# env vars like the daily scan's hour/minute - unlike the daily scan time,
# there's no reason for these to vary per deployment.
INTRADAY_HOURS = "10-15"
INTRADAY_MINUTES = "0,30"
EOD_NUDGE_HOUR = 15
EOD_NUDGE_MINUTE = 15


def _run_once() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    cfg = load_config()
    tz = cfg.market_timezone

    def _scan_job() -> None:
        try:
            run_scan(cfg)
        except Exception:  # noqa: BLE001 - one bad run must never kill the scheduler
            logger.exception("Daily scan raised unexpectedly; will retry on the next scheduled run.")

    def _intraday_job() -> None:
        try:
            run_intraday_check(cfg)
        except Exception:  # noqa: BLE001
            logger.exception("Intraday check raised unexpectedly; will retry on the next scheduled run.")

    def _eod_job() -> None:
        try:
            run_end_of_day_nudge(cfg)
        except Exception:  # noqa: BLE001
            logger.exception("End-of-day nudge raised unexpectedly.")

    scheduler = BlockingScheduler(timezone=tz)
    scheduler.add_job(
        _scan_job,
        CronTrigger(day_of_week="mon-fri", hour=cfg.scan_hour, minute=cfg.scan_minute, timezone=tz),
        id="daily_scan", max_instances=1, misfire_grace_time=1800,
    )
    scheduler.add_job(
        _intraday_job,
        CronTrigger(day_of_week="mon-fri", hour=INTRADAY_HOURS, minute=INTRADAY_MINUTES, timezone=tz),
        id="intraday_check", max_instances=1, misfire_grace_time=600,
    )
    scheduler.add_job(
        _eod_job,
        CronTrigger(day_of_week="mon-fri", hour=EOD_NUDGE_HOUR, minute=EOD_NUDGE_MINUTE, timezone=tz),
        id="eod_nudge", max_instances=1, misfire_grace_time=600,
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
        "Investment tracker scheduler started: daily scan weekdays %02d:%02d %s, "
        "intraday checks every 30min %s:00-%s:30, EOD nudge %02d:%02d. Ctrl+C/SIGTERM to stop.",
        cfg.scan_hour, cfg.scan_minute, tz, INTRADAY_HOURS.split("-")[0], INTRADAY_HOURS.split("-")[1],
        EOD_NUDGE_HOUR, EOD_NUDGE_MINUTE,
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
