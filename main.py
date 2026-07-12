#!/usr/bin/env python3
"""Entry point for the news sentiment trading bot (Alpaca paper trading).

Usage:
    python main.py                 # start scheduler: every 30 min, market hours
    python main.py --once          # run one cycle now and exit (good for testing)
    python main.py --eod           # write an end-of-day performance report now and exit
    python main.py --config PATH   # use an alternate config.ini (optional; env vars
                                    #   always take precedence — see .env.example)

Configuration is env-vars-first: on Railway (or anywhere else) you do NOT need
a config.ini — set environment variables in the platform's dashboard. A
config.ini is only ever an optional local-dev convenience.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from datetime import datetime, timezone

from bot.config import load_config
from bot.logging_utils import ClosedTradeLogger, DailySummaryLogger, TradeLogger
from bot.news import get_news_provider
from bot.notifications import NotificationService, TelegramNotifier
from bot.notifications.summary import build_queued_summary
from bot.persistence import Recorder
from bot.reporting import PerformanceReporter
from bot.scheduler import start_scheduler
from bot.sentiment import get_sentiment_analyzer
from bot.state import BotState
from bot.trading import AlpacaBroker, RiskManager, SentimentStrategy
from bot.universe import get_universe_provider
from bot.utils.logging_setup import setup_logging

# NOTE: SIGTERM/SIGINT handling lives in bot/scheduler.py, which owns the
# long-running BlockingScheduler and needs the signal to actually interrupt
# scheduler.start(). One-shot modes (--once/--check/--eod/--force) run
# synchronously and exit on their own, so no separate handler is needed here.
_shutdown = threading.Event()


def _maybe_start_health_server(port) -> None:
    """Railway's 'Web Service' deploy type expects the process to bind $PORT
    and respond to HTTP requests, or it may mark the deploy unhealthy. This
    bot is a background worker with nothing to serve, but running it as a
    Web Service is a common (and reasonable) Railway setup, so we bind a
    trivial health endpoint when PORT is present. If you deploy this as a
    'Worker' service instead (recommended), PORT won't be set and this is a
    no-op."""
    if port is None:
        return
    import http.server

    log = logging.getLogger("health")

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib method name
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, fmt, *args):  # noqa: A003 - silence per-request logs
            pass

    def _serve():
        try:
            server = http.server.HTTPServer(("0.0.0.0", port), _Handler)
            log.info("Health check server listening on :%d", port)
            server.serve_forever()
        except OSError as exc:
            log.warning("Health server could not bind port %d: %s", port, exc)

    threading.Thread(target=_serve, daemon=True, name="health-server").start()


def build(cfg):
    broker = AlpacaBroker(
        cfg.alpaca.api_key, cfg.alpaca.secret_key, cfg.alpaca.paper,
        retry_attempts=cfg.retry.max_attempts, retry_base_delay=cfg.retry.base_delay_seconds,
    )
    universe = get_universe_provider(cfg)
    news = get_news_provider(cfg)
    analyzer = get_sentiment_analyzer(cfg)
    risk = RiskManager(cfg.risk)
    trade_logger = TradeLogger(cfg.logging.trade_log_path)
    closed_trade_logger = ClosedTradeLogger(cfg.logging.closed_trades_path)
    summary_logger = DailySummaryLogger(
        cfg.logging.daily_summary_path, cfg.logging.trade_log_path
    )
    state = BotState(cfg.logging.state_path)
    reporter = PerformanceReporter(
        broker, cfg.logging.trade_log_path, cfg.logging.closed_trades_path, cfg.logging.report_dir
    )
    # Dashboard persistence: purely additive observability, no bearing on
    # trading decisions. No-ops automatically if DATABASE_URL isn't set.
    telegram = TelegramNotifier(cfg.telegram.bot_token, cfg.telegram.chat_id) if cfg.telegram.enabled else TelegramNotifier("", "")
    recorder = NotificationService(Recorder(), telegram=telegram)
    strategy = SentimentStrategy(
        cfg, broker, universe, news, analyzer, risk, trade_logger, summary_logger, state,
        closed_trade_logger=closed_trade_logger, recorder=recorder,
    )
    return broker, summary_logger, reporter, strategy, recorder


def do_check(cfg) -> int:
    """Verify the bot can connect and read data, without placing any orders.

    Safe to run any time (even with the market closed). Prints your paper
    account balance plus a sample news+sentiment read so you know the whole
    pipeline is wired correctly.
    """
    log = logging.getLogger("check")
    ok = True

    # 1) Alpaca account / trading connectivity
    try:
        broker = AlpacaBroker(
            cfg.alpaca.api_key, cfg.alpaca.secret_key, cfg.alpaca.paper,
            retry_attempts=cfg.retry.max_attempts, retry_base_delay=cfg.retry.base_delay_seconds,
        )
        acct = broker.account_snapshot()
        is_open = broker.is_market_open()
        positions = broker.open_positions()
        log.info("[OK] Alpaca connected | portfolio=$%.2f cash/bp=$%.2f equity=$%.2f | "
                 "market_open=%s | open_positions=%d",
                 acct["portfolio_value"], acct["buying_power"], acct["equity"],
                 is_open, len(positions))
    except Exception as exc:  # noqa: BLE001
        log.error("[FAIL] Alpaca connection/account: %s", exc)
        log.error("       -> check ALPACA_API_KEY / ALPACA_SECRET_KEY (paper keys).")
        return 1  # nothing else will work without this

    # 2) News read (a sample ticker)
    sample = "AAPL"
    try:
        news = get_news_provider(cfg)
        articles = news.fetch(sample, cfg.news.lookback_hours, cfg.news.max_articles_per_symbol)
        log.info("[OK] News (%s) | pulled %d headlines for %s",
                 cfg.news.provider, len(articles), sample)
    except Exception as exc:  # noqa: BLE001
        ok = False
        articles = []
        log.error("[FAIL] News provider '%s': %s", cfg.news.provider, exc)

    # 3) Sentiment read on those headlines
    try:
        analyzer = get_sentiment_analyzer(cfg)
        result = analyzer.analyze(sample, articles)
        log.info("[OK] Sentiment (%s) | %s score=%.1f (%s) from %d headlines",
                 cfg.sentiment.provider, sample, result.score, result.label,
                 result.article_count)
    except Exception as exc:  # noqa: BLE001
        ok = False
        log.error("[FAIL] Sentiment provider '%s': %s", cfg.sentiment.provider, exc)

    # 4) Telegram (optional - only checked if credentials are configured)
    if cfg.telegram.enabled:
        telegram = TelegramNotifier(cfg.telegram.bot_token, cfg.telegram.chat_id)
        if telegram.test():
            log.info("[OK] Telegram | test message sent")
        else:
            ok = False
            log.error("[FAIL] Telegram | could not send test message — check "
                      "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
    else:
        log.info("[SKIP] Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID unset)")

    if ok:
        log.info("CHECK PASSED — keys connect and the read pipeline works. "
                 "No orders were placed.")
        return 0
    log.warning("CHECK FINISHED WITH WARNINGS — Alpaca is fine but a provider above failed.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="News sentiment paper-trading bot")
    parser.add_argument("--config", default="config.ini",
                        help="optional local config.ini path; env vars always take precedence")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    parser.add_argument("--force", action="store_true",
                        help="run one cycle now, IGNORING market hours (always a safe "
                             "dry-run simulation; places no orders)")
    parser.add_argument("--check", action="store_true",
                        help="verify keys connect + print paper balance, then exit")
    parser.add_argument("--eod", action="store_true",
                        help="write the daily performance report now and exit")
    args = parser.parse_args()

    try:
        cfg = load_config(args.config)
    except ValueError as exc:
        # Configuration errors are the #1 cause of a crash-loop on a fresh
        # deploy. Print clearly to stderr (before logging is even set up)
        # so it's the first thing visible in Railway's deploy logs.
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    setup_logging(cfg.logging.log_level, cfg.logging.run_log_path, cfg.logging.log_format)
    log = logging.getLogger("main")
    _maybe_start_health_server(cfg.server.port)

    if args.check:
        return do_check(cfg)

    # --force is a safe simulation: never submit orders, even if dry_run=false.
    if args.force:
        cfg.risk.dry_run = True

    mode = "DRY RUN (no orders submitted)" if cfg.risk.dry_run else "LIVE PAPER ORDERS"
    config_source = f"config.ini ({cfg.config_file_used}) + env vars" if cfg.config_file_used else "environment variables only"
    log.info("Starting | universe=%s news=%s sentiment=%s | %s | config source: %s",
             cfg.universe.provider, cfg.news.provider, cfg.sentiment.provider, mode, config_source)

    try:
        broker, summary_logger, reporter, strategy, recorder = build(cfg)
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to initialize the bot: %s", exc)
        # Best-effort: even init failures are worth a row if the DB happens
        # to be reachable independently (e.g. a bad news/sentiment key, not
        # a DB problem). No-ops if DATABASE_URL isn't set. Telegram is sent
        # directly here since the full NotificationService/Recorder pairing
        # failed to build.
        NotificationService(
            Recorder(),
            telegram=TelegramNotifier(cfg.telegram.bot_token, cfg.telegram.chat_id)
            if cfg.telegram.enabled else TelegramNotifier("", ""),
        ).record_notification(
            type_="error", severity="critical", title="Bot failed to start",
            message=str(exc),
        )
        return 1

    _commit = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")
    _env = os.environ.get("RAILWAY_ENVIRONMENT_NAME", "")
    _deploy_bits = " | ".join(
        b for b in [f"commit={_commit[:7]}" if _commit else "", f"env={_env}" if _env else ""] if b
    )
    recorder.record_notification(
        type_="bot_restart", severity="info", title="Bot started",
        message=f"universe={cfg.universe.provider} news={cfg.news.provider} "
                f"sentiment={cfg.sentiment.provider} mode={mode}"
                + (f" | {_deploy_bits}" if _deploy_bits else ""),
    )

    def run_eod():
        pv = broker.account_snapshot()["portfolio_value"]
        summary_logger.write_eod(pv)
        text = reporter.write()
        # Roll up anything the user configured as "daily summary only" in
        # the dashboard's Notification Settings (see bot/notifications/summary.py)
        # into this same message, so muting immediate sends doesn't mean
        # those events vanish from Telegram entirely.
        queued = build_queued_summary(recorder.database_url, "daily_summary", period_label="today")
        if queued:
            text = f"{text}\n\n{queued}"
        log.info("\n%s", text)
        recorder.record_notification(
            type_="daily_summary", severity="info", title="Daily summary generated",
            message=text,
        )
        # Weekly rollup: piggybacks on the existing daily EOD job, only does
        # anything extra on Fridays. Purely additive observability - no
        # bearing on trading decisions.
        if datetime.now(timezone.utc).weekday() == 4:
            weekly_text = reporter.write_weekly()
            queued_weekly = build_queued_summary(
                recorder.database_url, "weekly_summary", period_label="this week"
            )
            if queued_weekly:
                weekly_text = f"{weekly_text}\n\n{queued_weekly}"
            log.info("\n%s", weekly_text)
            recorder.record_notification(
                type_="weekly_summary", severity="info", title="Weekly summary generated",
                message=weekly_text,
            )

    if args.eod:
        run_eod()
        return 0
    if args.force:
        strategy.run_cycle(force=True, scheduler_status="one_shot")
        return 0
    if args.once:
        strategy.run_cycle(scheduler_status="one_shot")
        return 0

    def run_cycle_safely():
        # Guards against a bug in a single cycle taking the scheduler job
        # down silently (APScheduler swallows exceptions from jobs on its
        # own). Per-symbol errors are already isolated inside run_cycle();
        # this is the outer net for anything else (e.g. summary logging).
        try:
            strategy.run_cycle(scheduler_status="scheduled")
        except Exception as exc:  # noqa: BLE001
            log.exception("Critical error in scheduled cycle: %s", exc)
            recorder.notify_critical_error("scheduled cycle", str(exc))

    def on_scheduler_crash(reason: str) -> None:
        recorder.notify_scheduler_failure(reason)

    try:
        start_scheduler(
            cfg, run_cycle_safely,
            eod_fn=run_eod, shutdown_event=_shutdown, on_crash=on_scheduler_crash,
        )
    except Exception as exc:  # noqa: BLE001 - truly unhandled; last-resort alert before exit
        log.exception("Bot process is exiting due to an unhandled error: %s", exc)
        recorder.notify_bot_stopped_unexpectedly(str(exc))
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
