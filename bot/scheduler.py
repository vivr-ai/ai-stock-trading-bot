"""Scheduling: run the strategy at configured minute marks during market hours,
plus an end-of-day performance report just after the close.

The cron window (weekdays 09:00-16:00 ET) is a coarse filter; the strategy then
double-checks Alpaca's clock so holidays and early closes are handled correctly.

Resilience: this owns SIGTERM/SIGINT handling (Railway sends SIGTERM on
redeploy/restart, not just SIGINT) so a routine platform restart shuts the
scheduler down cleanly instead of being killed mid-cycle. If the scheduler
itself dies from something unexpected (not a deliberate stop), this logs it
and restarts a fresh scheduler after a backoff instead of letting the whole
process exit — the point of running unattended is that a hiccup here
shouldn't need a human to notice and manually restart the deploy.
"""
from __future__ import annotations

import logging
import signal
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def start_scheduler(cfg, run_cycle_fn: Callable, eod_fn: Optional[Callable] = None,
                     shutdown_event=None) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    tz = cfg.schedule.market_timezone
    minutes = ",".join(str(m) for m in cfg.schedule.run_minutes)
    stop_requested = {"flag": False}

    def _run_once() -> None:
        scheduler = BlockingScheduler(timezone=tz)

        scheduler.add_job(
            run_cycle_fn,
            CronTrigger(day_of_week="mon-fri", hour="9-15", minute=minutes, timezone=tz),
            id="cycle", max_instances=1, misfire_grace_time=120,
        )
        # Catch the 16:00 marks too (cron hour ranges are whole-hour inclusive).
        scheduler.add_job(
            run_cycle_fn,
            CronTrigger(day_of_week="mon-fri", hour="16", minute="0", timezone=tz),
            id="cycle_close", max_instances=1, misfire_grace_time=120,
        )
        if eod_fn is not None:
            scheduler.add_job(
                eod_fn,
                CronTrigger(day_of_week="mon-fri", hour="16", minute="5", timezone=tz),
                id="eod_summary", max_instances=1, misfire_grace_time=300,
            )

        def _handle_stop(signum, _frame):
            logger.info("Received signal %s; stopping scheduler.", signum)
            stop_requested["flag"] = True
            scheduler.shutdown(wait=False)

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _handle_stop)
            except (ValueError, OSError):
                pass  # e.g. not the main thread — best-effort only

        logger.info(
            "Scheduler started: weekdays, minutes [%s], 09:00-16:00 %s. "
            "Ctrl+C or SIGTERM to stop.", minutes, tz,
        )
        scheduler.start()  # blocks until shutdown() is called or an exception escapes

    backoff = 10
    while True:
        try:
            _run_once()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Scheduler stopped.")
            return
        except Exception as exc:  # noqa: BLE001 - must never take the whole process down
            logger.exception("Scheduler crashed unexpectedly: %s", exc)
            if stop_requested["flag"] or (shutdown_event is not None and shutdown_event.is_set()):
                return
            logger.warning("Restarting scheduler in %ds.", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
            continue
        return  # scheduler.start() returned normally (we called shutdown()) — done
