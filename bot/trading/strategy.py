"""One strategy cycle.

Per cycle:
  1. Build the candidate universe (static, or top-N most mentioned).
  2. Snapshot the account once (with retries). If that fails, skip the cycle.
  3. Detect names that exited since last cycle (bracket fills) -> cooldown +
     realized P/L logged to the closed-trade ledger.
  4. Check the market filters (don't buy into a falling market / below its
     long-term trend).
  5. For each symbol: headlines -> sentiment -> confirmation filters -> risk
     -> order/log.
  6. Append a cycle line to the daily summary log.

Buy rule (ALL must hold):
  * market not down more than market_filter_max_drop_pct today, AND SPY (or
    the configured proxy) is above its own market_regime_ma_period-day SMA
  * not already held, no live order pending, not in re-entry cooldown
  * sentiment score >= buy_threshold (+8) with >= min_headlines (5) headlines
  * price above its sma_period-day SMA (require_price_above_sma)
  * today's volume-so-far >= min_volume_ratio x the volume normally expected
    by THIS POINT in the trading session - its volume_lookback_days-day
    average scaled down by the fraction of the session elapsed (a 10am
    check is compared to ~10% of a full day's average, not the full
    average - see AlpacaBroker.market_snapshot / _session_elapsed_fraction).
    Comparing to the raw full-day average made this gate nearly impossible
    to clear before mid-afternoon regardless of how strong a stock's volume
    genuinely was; fixed August 2026 after multiple days of score 9-10
    candidates (AMZN, MSFT) still under 0.6x with 30 minutes left to trade.
  * NOT already up more than max_intraday_runup_pct since yesterday's close
  * under the per-cycle new-position cap AND the per-sector cap
  * passes the risk manager (size, position count, exposure cap)

Sell rule:
  * held AND score < sell_threshold (-5) AND >= sell_min_headlines headlines
  * the -10% / +20% price exits are GTC bracket legs that fire at Alpaca;
    when they fire, the position simply "disappears" between cycles — caught
    by BotState.detect_exits() and logged to the closed-trade ledger from
    there, since we didn't place that closing order ourselves.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from ..sentiment.base import SentimentResult
from ..universe.static_universe import sector_of

logger = logging.getLogger(__name__)

# Rule-of-thumb bar for "enough shadow data to form a view on Path B" - kept
# in sync BY HAND with the dashboard's own copy of these same two numbers
# (dashboard/app/shadow-comparison/page.tsx's MIN_CLOSED_FOR_A_READ /
# MIN_WEEKS_FOR_A_READ). There's no shared config surface between the Python
# bot and the TypeScript dashboard, so if one changes, change the other too.
SHADOW_VERDICT_MIN_CLOSED = 20
SHADOW_VERDICT_MIN_WEEKS = 4.0


@dataclass
class CycleStats:
    evaluated: int = 0
    buys: int = 0
    sells: int = 0
    blocked: int = 0


class _NullRecorder:
    """Stand-in used when no dashboard Recorder is configured, so call
    sites never need to check for None."""

    def __getattr__(self, _name):
        return lambda *a, **k: None


class SentimentStrategy:
    def __init__(self, cfg, broker, universe, news, analyzer, risk,
                 trade_logger, summary_logger, state, closed_trade_logger=None,
                 recorder=None, version_provider=None, bot_control_provider=None):
        self.cfg = cfg
        self.broker = broker
        self.universe = universe
        self.news = news
        self.analyzer = analyzer
        self.risk = risk
        self.trade_logger = trade_logger
        self.summary_logger = summary_logger
        self.state = state
        self.closed_trade_logger = closed_trade_logger
        # Optional dashboard recorder. A no-op stand-in keeps every call site
        # below simple (no "if self.recorder" checks needed) when no DB is
        # configured - see bot/persistence/db.py.
        self.recorder = recorder if recorder is not None else _NullRecorder()
        # Read-only mirror of the dashboard's active strategy_versions row
        # (see bot/strategy_version.py) - falls back to "v1" if unset/
        # unreachable. The bot never creates or activates a version itself;
        # this only tags trades with whichever version a human already made
        # active on the dashboard.
        from ..strategy_version import StrategyVersionProvider
        self.version_provider = version_provider or StrategyVersionProvider(
            getattr(recorder, "database_url", None)
        )
        # Read-only mirror of the dashboard's bot_control row (see
        # bot/bot_control.py). Fails open (not paused) if unset/unreachable -
        # a DB hiccup should never silently halt trading. Pausing/resuming
        # is exclusively a dashboard action; the bot never sets this itself.
        from ..bot_control import BotControlProvider
        self.bot_control_provider = bot_control_provider or BotControlProvider(
            getattr(recorder, "database_url", None)
        )
        # Guards the daily-loss-limit notification so it fires once per
        # breach-day, not every 30-min cycle for the rest of the day.
        self._daily_loss_notified_date: Optional[str] = None
        # Same one-per-day guard for the PDT warning (see _maybe_notify_pdt).
        self._pdt_notified_date: Optional[str] = None
        # Tracks the last-seen pause state so the bot_paused/bot_resumed
        # notification fires once per state CHANGE, not every cycle while
        # paused (or every cycle once resumed).
        self._last_known_paused: bool = False
        # Same one-per-CHANGE pattern for AlpacaBroker.last_clock_degraded
        # (see alpaca_client.py's is_market_open): fires a "broker_issue"
        # alert once when Alpaca's clock endpoint starts failing over to the
        # local fallback, and a recovery note once it stops, instead of
        # spamming Telegram every 30 min while degraded.
        self._last_clock_degraded: bool = False

    def run_cycle(self, force: bool = False, scheduler_status: str = "scheduled") -> None:
        market_open = self.broker.is_market_open()
        self._maybe_notify_clock_degraded()
        if not force and not market_open:
            logger.info("Market closed; skipping cycle.")
            self.recorder.record_heartbeat(
                status="running", scheduler_status=scheduler_status, market_open=False,
                dry_run=self.cfg.risk.dry_run, message="market closed; cycle skipped",
            )
            return
        if force:
            logger.info("FORCE: running one cycle ignoring market hours (test mode).")

        api_call_started = time.monotonic()
        try:
            acct = self.broker.account_snapshot()
            api_latency_ms = (time.monotonic() - api_call_started) * 1000.0
            open_positions = self.broker.open_positions()
            pending = self.broker.pending_order_symbols()
            exposure = self.broker.total_exposure()
        except Exception as exc:  # noqa: BLE001
            api_latency_ms = (time.monotonic() - api_call_started) * 1000.0
            logger.error("Account snapshot failed; skipping this cycle: %s", exc)
            self.recorder.record_heartbeat(
                status="error", scheduler_status=scheduler_status,
                dry_run=self.cfg.risk.dry_run, message=f"account snapshot failed: {exc}",
                api_latency_ms=api_latency_ms,
            )
            self.recorder.record_notification(
                type_="broker_issue", severity="warning", title="Account snapshot failed",
                message=str(exc),
            )
            return

        market_regime, cached_spy_snap = self._compute_market_regime()

        self.recorder.record_heartbeat(
            status="running", scheduler_status=scheduler_status, market_open=True,
            dry_run=self.cfg.risk.dry_run, portfolio_value=acct.get("portfolio_value"),
            cash=acct.get("cash"), equity=acct.get("equity"),
            buying_power=acct.get("buying_power"), open_positions=len(open_positions),
            api_latency_ms=api_latency_ms, trading_mode=self.cfg.trading.mode,
            daytrade_count=acct.get("daytrade_count"),
            pattern_day_trader=acct.get("pattern_day_trader"),
            market_regime=market_regime,
        )
        self._maybe_notify_pdt(acct)
        self.recorder.record_portfolio_snapshot(
            portfolio_value=acct.get("portfolio_value"), cash=acct.get("cash"),
            equity=acct.get("equity"), buying_power=acct.get("buying_power"),
            unrealized_pl=None, open_positions=len(open_positions), exposure=exposure,
        )
        self._sync_open_positions_snapshot(acct.get("portfolio_value"))
        self._adopt_legacy_positions()

        exited = self.state.detect_exits(list(open_positions.keys()))
        if exited:
            logger.info("Detected exits since last cycle (cooldown started): %s",
                        ", ".join(exited))
            for sym in exited:
                self._log_auto_exit(sym)

        # Manual pause / Emergency Stop, set from the dashboard (see
        # bot/bot_control.py). Same "block new entries only" semantics as
        # every other gate here - sentiment sells and price brackets keep
        # managing existing positions. Checked first since it's the cheapest
        # (no API call) and most likely to be the actual reason on a given
        # cycle when it's set.
        is_paused, pause_reason = self.bot_control_provider.is_paused()
        if is_paused != self._last_known_paused:
            self._last_known_paused = is_paused
            if is_paused:
                logger.warning("Trading paused from dashboard%s; sentiment exits only this cycle.",
                               f" ({pause_reason})" if pause_reason else "",
                               extra={"decision": "block_new_entries", "reason": "manual_pause"})
                self.recorder.record_notification(
                    type_="bot_paused", severity="warning", title="Trading paused",
                    message=(pause_reason or "Paused from the dashboard.")
                    + " New entries are blocked; existing positions keep being managed.",
                )
            else:
                logger.info("Trading resumed from dashboard.")
                self.recorder.record_notification(
                    type_="bot_resumed", severity="info", title="Trading resumed",
                    message="New entries are allowed again.",
                )
        if is_paused:
            self.recorder.record_decision(symbol="*", decision="block_new_entries",
                                          reason="manual_pause")

        new_entries_allowed = not is_paused and not self.risk.daily_loss_breached(
            acct["equity"], acct["last_equity"]
        )
        if not new_entries_allowed and not is_paused:
            logger.warning("Daily loss limit breached; sentiment exits only this cycle.",
                           extra={"decision": "block_new_entries", "reason": "daily_loss_limit"})
            self.recorder.record_decision(symbol="*", decision="block_new_entries",
                                          reason="daily_loss_limit")
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self._daily_loss_notified_date != today:
                self._daily_loss_notified_date = today
                loss_pct = None
                if acct.get("last_equity"):
                    loss_pct = (acct["equity"] - acct["last_equity"]) / acct["last_equity"] * 100.0
                self.recorder.record_notification(
                    type_="daily_loss_limit", severity="warning",
                    title="Daily loss limit reached",
                    message=(
                        f"Kill switch engaged (limit {self.cfg.risk.daily_loss_limit_pct}%). "
                        f"Sentiment-driven exits still run; no new entries today."
                        + (f" Today's change: {loss_pct:.2f}%." if loss_pct is not None else "")
                    ),
                )

        # Market filter 1: if the broad market is down hard today, don't open longs.
        if new_entries_allowed and self.cfg.strategy.market_filter_max_drop_pct > 0:
            mkt = self.broker.market_change_pct(self.cfg.strategy.market_filter_symbol)
            if mkt is not None and mkt <= -self.cfg.strategy.market_filter_max_drop_pct:
                logger.warning(
                    "Market filter: %s down %.2f%% today; pausing new entries.",
                    self.cfg.strategy.market_filter_symbol, mkt,
                    extra={"decision": "block_new_entries", "reason": "intraday_drop",
                           "symbol": self.cfg.strategy.market_filter_symbol, "change_pct": mkt},
                )
                self.recorder.record_decision(
                    symbol=self.cfg.strategy.market_filter_symbol, decision="block_new_entries",
                    reason="intraday_drop", change_pct=mkt,
                )
                new_entries_allowed = False

        # Market filter 2 (regime): skip all new entries if the market proxy
        # (SPY by default) is below its own long-term (50-day) SMA — i.e. the
        # broad market is in a downtrend, not just a single bad day.
        if new_entries_allowed and self.cfg.strategy.market_regime_filter_enabled:
            # Reuse the snapshot _compute_market_regime() already fetched
            # this cycle (same symbol/sma_period/volume_lookback_days) rather
            # than hitting the API again for the same data.
            spy_snap = cached_spy_snap
            if spy_snap is None or spy_snap.sma is None:
                logger.warning(
                    "Market regime filter: could not read %s's %d-day SMA; "
                    "pausing new entries (fail-closed).",
                    self.cfg.strategy.market_filter_symbol, self.cfg.strategy.market_regime_ma_period,
                    extra={"decision": "block_new_entries", "reason": "regime_data_unavailable"},
                )
                self.recorder.record_decision(
                    symbol=self.cfg.strategy.market_filter_symbol, decision="block_new_entries",
                    reason="regime_data_unavailable",
                )
                new_entries_allowed = False
            elif spy_snap.last < spy_snap.sma:
                logger.warning(
                    "Market regime filter: %s (%.2f) below its %d-day SMA (%.2f); "
                    "pausing new entries.",
                    self.cfg.strategy.market_filter_symbol, spy_snap.last,
                    self.cfg.strategy.market_regime_ma_period, spy_snap.sma,
                    extra={"decision": "block_new_entries", "reason": "below_market_regime_sma",
                           "symbol": self.cfg.strategy.market_filter_symbol,
                           "price": spy_snap.last, "sma": spy_snap.sma},
                )
                self.recorder.record_decision(
                    symbol=self.cfg.strategy.market_filter_symbol, decision="block_new_entries",
                    reason="below_market_regime_sma", price=spy_snap.last, sma=spy_snap.sma,
                )
                new_entries_allowed = False

        # Current sector concentration, so we don't pile into one correlated group.
        sector_counts = Counter(sector_of(s) for s in open_positions)

        symbols = self.universe.get_universe(self.cfg.universe.top_n)
        stats = CycleStats()

        for symbol in symbols:
            try:
                exposure = self._process_symbol(
                    symbol, acct, exposure, open_positions, pending,
                    sector_counts, new_entries_allowed, stats, market_regime,
                )
            except Exception as exc:  # noqa: BLE001 - isolate per-symbol failures
                logger.exception("Error processing %s: %s", symbol, exc,
                                 extra={"symbol": symbol, "decision": "error"})

        self._maybe_notify_shadow_verdict_ready()

        self.summary_logger.log_cycle(
            portfolio_value=acct["portfolio_value"],
            open_positions=len(open_positions),
            exposure=exposure,
            stats=stats,
        )

    def _compute_market_regime(self):
        """Classify the current market backdrop for Strategy Intelligence's
        win-rate-by-regime breakdown (and tags every trade entered this
        cycle). Volatility extremes take priority over trend - a 2%+ day
        matters more for risk context than which side of the SMA the market
        happens to be on. This is a lightweight heuristic (SPY's own daily
        change_pct as a volatility proxy, price vs its N-day SMA for trend),
        not a dedicated realized-volatility or VIX-based classifier - good
        enough for pattern discovery, not a claim of precision.

        Returns (regime_label_or_None, market_snapshot_or_None) - the
        snapshot is returned too so the market-regime FILTER below can reuse
        it instead of making a second identical API call this cycle.
        """
        try:
            snap = self.broker.market_snapshot(
                self.cfg.strategy.market_filter_symbol,
                sma_period=self.cfg.strategy.market_regime_ma_period,
                volume_lookback_days=self.cfg.strategy.market_regime_ma_period,
                session_tz=self.cfg.schedule.market_timezone,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not compute market regime: %s", exc)
            return None, None
        if snap is None or snap.change_pct is None:
            return None, snap
        change_pct = snap.change_pct
        if abs(change_pct) >= 2.0:
            return "high_volatility", snap
        if snap.sma is None:
            return None, snap
        near_sma = snap.sma > 0 and abs(snap.last - snap.sma) / snap.sma <= 0.005
        if abs(change_pct) <= 0.3 and near_sma:
            return "low_volatility", snap
        if snap.last > snap.sma * 1.005:
            return "bull", snap
        if snap.last < snap.sma * 0.995:
            return "bear", snap
        return "sideways", snap

    def _maybe_notify_pdt(self, acct) -> None:
        """Pattern Day Trader warning: FINRA restricts accounts under $25,000
        equity to 3 day trades per rolling 5-business-day window before
        they're flagged and blocked from opening new positions. Alpaca's
        `daytrade_count` / `pattern_day_trader` fields (surfaced by
        account_snapshot()) are the authoritative source - it enforces the
        rule, this just warns before it bites. Paper accounts don't
        meaningfully enforce PDT, but the count is still shown for
        awareness; the warning fires regardless of mode since it's useful
        practice for anyone planning to go live. Fires at most once per
        calendar day, same guard pattern as the daily-loss-limit notice."""
        daytrade_count = acct.get("daytrade_count")
        pattern_day_trader = acct.get("pattern_day_trader")
        equity = acct.get("equity")
        if daytrade_count is None:
            return
        approaching = daytrade_count >= 3  # the 4th day trade triggers the flag
        under_25k = equity is not None and equity < 25000
        if not (approaching or pattern_day_trader):
            return
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._pdt_notified_date == today:
            return
        self._pdt_notified_date = today
        if pattern_day_trader:
            title = "Pattern Day Trader flag is ACTIVE on this account"
            severity = "critical"
            detail = ("Alpaca has flagged this account as a Pattern Day Trader. "
                      "New positions may be restricted until equity is at or above "
                      "$25,000." if under_25k else
                      "Alpaca has flagged this account as a Pattern Day Trader.")
        else:
            title = f"Approaching Pattern Day Trader limit ({daytrade_count}/4 day trades)"
            severity = "warning"
            detail = (
                f"{daytrade_count} day trades in the current rolling 5-business-day window. "
                "One more day trade will flag this account as a Pattern Day Trader"
                + (", which restricts trading below $25,000 equity." if under_25k else ".")
            )
        self.recorder.record_notification(
            type_="pdt_warning", severity=severity, title=title,
            message=f"{detail} mode={self.cfg.trading.mode} equity={equity}",
        )

    def _maybe_notify_clock_degraded(self) -> None:
        """Alert once when AlpacaBroker.is_market_open() has to fall back to
        the local weekday/hours check because Alpaca's own /v2/clock
        endpoint is unreachable (see alpaca_client.py), and once more when it
        recovers - same one-per-state-CHANGE pattern as
        bot_paused/bot_resumed below, so this doesn't spam Telegram every
        30-min cycle for as long as the outage lasts. Uses the existing
        "broker_issue" notification type (see bot/notifications/settings.py)
        so it's grouped with other broker-connectivity alerts and honors
        whatever channel the dashboard has configured for that type."""
        degraded = getattr(self.broker, "last_clock_degraded", False)
        if degraded and not self._last_clock_degraded:
            self._last_clock_degraded = True
            self.recorder.record_notification(
                type_="broker_issue", severity="warning",
                title="Alpaca market-clock check failing",
                message=(
                    "Could not fetch the market clock from Alpaca after retries; "
                    "falling back to a local weekday/hours check instead of "
                    "assuming the market is closed, so scan cycles continue "
                    "during real trading hours. See deploy logs for the "
                    "underlying Alpaca error."
                ),
            )
        elif not degraded and self._last_clock_degraded:
            self._last_clock_degraded = False
            self.recorder.record_notification(
                type_="broker_issue", severity="info",
                title="Alpaca market-clock check recovered",
            )

    def _sync_open_positions_snapshot(self, portfolio_value) -> None:
        """Push the full current book (with unrealized P/L straight from
        Alpaca, plus the entry reason/confidence recorded at buy time) to
        the dashboard's open_positions table. Best-effort: any failure here
        never affects trading, only what the dashboard shows.

        ai_confidence/entry_reason/entry_time are read from the durable
        `trades` table (Recorder.get_last_buy_trade) - NOT
        bot/state.py's ephemeral open-lot JSON, which is wiped on every
        Railway redeploy. That was the previous source, and it's exactly
        why the Portfolio page could show these for a symbol bought after
        the last redeploy but blank for anything held across one. Falls
        back to the ephemeral lot only if there's no durable buy row at
        all (e.g. a manually-adopted position with no trades-table
        history, or the DB briefly unreachable)."""
        try:
            detailed = self.broker.open_positions_detailed()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch detailed positions for dashboard sync: %s", exc)
            return
        rows = []
        for symbol, pos in detailed.items():
            lot = self.state.peek_open(symbol) or {}
            try:
                buy = self.recorder.get_last_buy_trade(symbol) or {}
            except Exception as exc:  # noqa: BLE001
                logger.warning("Durable buy-row lookup failed for %s (falling back to "
                               "ephemeral state): %s", symbol, exc)
                buy = {}
            # Explicit None-checks, not `or`/`.get(k, default)` - a real
            # sentiment_score of 0.0 is falsy but valid, and a present key
            # with a None value (an adopted/backfilled buy row with no
            # sentiment data) must still fall through to the ephemeral lot.
            ai_confidence = buy.get("sentiment_score")
            if ai_confidence is None:
                ai_confidence = lot.get("sentiment_score")
            entry_reason = buy.get("reason")
            if entry_reason is None:
                entry_reason = lot.get("reason")
            entry_time = buy["ts"].timestamp() if buy.get("ts") else lot.get("entry_time")
            allocation_pct = (
                (pos.market_value / portfolio_value * 100.0)
                if portfolio_value else None
            )
            rows.append({
                "symbol": symbol, "qty": pos.qty, "avg_entry_price": pos.avg_entry_price,
                "current_price": pos.current_price, "market_value": pos.market_value,
                "unrealized_pl": pos.unrealized_pl, "unrealized_plpc": pos.unrealized_plpc,
                "allocation_pct": allocation_pct, "ai_confidence": ai_confidence,
                "entry_reason": entry_reason, "entry_time": entry_time,
            })
        self.recorder.sync_open_positions(rows)

    def _adopt_legacy_positions(self) -> None:
        """Positions that exist at the broker but weren't bought through this
        bot - manually opened, or predating this codebase's trade tracking
        (the Aug 2026 NVDA/TSLA/AAPL incident: real positions sitting with no
        stop-loss and no trades-table row, because submit_bracket_buy only
        attaches a bracket at the moment THIS bot buys something) - get two
        things backfilled here: a protective stop/take-profit order if one
        isn't already live, and a 'buy' row in the trades table if one is
        missing (needed for P/L reporting on exit - see
        _log_closed_trade_from_history - and for the Portfolio page's Stop
        Loss column, which reads the latest buy row's stop_price/take_profit).

        Runs every cycle but is cheap and a no-op once both are in place for
        every held symbol - not gated behind new_entries_allowed/pause since
        this manages risk on EXISTING positions, not new entries (same
        philosophy as the sentiment-driven sell path elsewhere in this file)."""
        try:
            detailed = self.broker.open_positions_detailed()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Legacy-position adoption: could not fetch open positions: %s", exc)
            return
        if not detailed:
            return

        protected = self.broker.get_protected_symbols()

        for symbol, pos in detailed.items():
            has_buy_row = self.recorder.get_last_buy_trade(symbol) is not None
            needs_protection = symbol not in protected
            if has_buy_row and not needs_protection:
                continue  # fully tracked and protected already

            entry_price = pos.avg_entry_price
            qty = pos.qty
            if not entry_price or entry_price <= 0 or not qty or qty <= 0:
                continue
            stop_price = round(entry_price * (1.0 - self.cfg.risk.stop_loss_pct / 100.0), 2)
            take_profit_price = round(entry_price * (1.0 + self.cfg.risk.take_profit_pct / 100.0), 2)

            protected_now = not needs_protection  # already had a bracket/OCO before this cycle
            protect_order_id = ""
            if needs_protection:
                if self.cfg.risk.dry_run:
                    logger.info(
                        "[DRY RUN] would protect adopted position %s: %.4g shares @ %.2f "
                        "(stop %.2f / tp %.2f)", symbol, qty, entry_price, stop_price, take_profit_price,
                    )
                else:
                    try:
                        order = self.broker.submit_protective_exit(
                            symbol, qty, stop_price, take_profit_price,
                            client_order_id=f"adopt-{symbol}-{int(time.time())}",
                        )
                        protected_now = True
                        protect_order_id = order.order_id
                        logger.info(
                            "Adopted %s: submitted protective stop/take-profit order %s "
                            "(stop %.2f / tp %.2f) for a position this bot didn't buy itself.",
                            symbol, order.order_id, stop_price, take_profit_price,
                            extra={"symbol": symbol, "decision": "adopt_protect",
                                   "order_id": order.order_id, "stop_price": stop_price,
                                   "take_profit_price": take_profit_price},
                        )
                        self.recorder.record_notification(
                            type_="position_adopted", severity="info",
                            title=f"Protected adopted position: {symbol}",
                            message=(
                                f"{symbol} was already held but had no stop-loss (bought outside "
                                f"this bot, or predates its trade tracking). Submitted a protective "
                                f"stop @ {stop_price:.2f} / take-profit @ {take_profit_price:.2f}, "
                                f"based on its entry price of {entry_price:.2f}."
                            ),
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Legacy-position protection failed for %s: %s", symbol, exc)
                        self.recorder.record_notification(
                            type_="error", severity="warning",
                            title=f"Could not protect adopted position: {symbol}",
                            message=str(exc),
                        )

            if not has_buy_row:
                fill = self.broker.last_fill(symbol, side="buy")
                reason = "adopted_legacy_position"
                rationale = (
                    "Already held when this bot's trade tracking started (or bought outside "
                    "the bot). Entry price is Alpaca's recorded average entry price for the "
                    "position; backfilled automatically so P/L reporting works normally on exit."
                )
                placeholder = SentimentResult(
                    symbol=symbol, score=0.0, label="unknown", rationale=rationale, article_count=0,
                )
                notional = entry_price * qty
                backfill_stop = stop_price if protected_now else 0.0
                backfill_tp = take_profit_price if protected_now else 0.0
                order_id_for_row = protect_order_id or (fill.order_id if fill else "")
                status = "adopted" if protected_now else "adopted_unprotected"
                # Deliberately NOT logged to self.trade_logger (the local CSV):
                # bot/reporting/performance.py's daily "Trades today" count
                # (and the EOD Telegram summary) sums that CSV's action=='buy'
                # rows for today's date - a backfill isn't a trading decision
                # made today, just bookkeeping catching up on one made outside
                # the bot, and counting it there would overstate the day's
                # real buy activity. record_trade below (the Postgres `trades`
                # table) still gets it, since that's what the Portfolio/Trade
                # History pages and get_last_buy_trade() need.
                self.recorder.record_trade(
                    action="buy", symbol=symbol, qty=qty, price=entry_price, notional=notional,
                    sentiment=placeholder, stop_price=backfill_stop, take_profit=backfill_tp,
                    reason=reason, rationale=rationale, dry_run=False,
                    order_id=order_id_for_row, status=status,
                    sector=sector_of(symbol), market_regime=None,
                )
                self.state.record_open(
                    symbol, entry_price, qty, reason=reason, sentiment_score=0.0,
                    sentiment_label="unknown", rationale=rationale, sector=sector_of(symbol),
                    market_regime=None, strategy_version=self.version_provider.current_version(),
                )
                logger.info(
                    "Adopted %s: backfilled trades-table entry (%.4g shares @ %.2f) so P/L "
                    "reporting and the dashboard work normally for this position going forward.",
                    symbol, qty, entry_price,
                    extra={"symbol": symbol, "decision": "adopt_backfill"},
                )

    def _log_closed_trade_from_history(self, symbol: str, exit_price: Optional[float],
                                        exit_reason: str) -> None:
        """Fallback exit-logging path, called whenever BotState has no
        open-lot record for `symbol` at exit time - almost always because
        bot/state.py's local JSON file was wiped by a Railway redeploy
        (ephemeral filesystem, no Volume attached) since this position was
        opened, NOT because the exit was already logged through the normal
        path. That second case is real too though (see _log_auto_exit's
        docstring), so before writing anything here we check whether a
        closed_trades row already exists for this symbol dated after its
        last buy - if so, the normal path already handled it and we skip,
        rather than double-counting one round-trip's P/L.

        Reconstructs entry price/qty/context from the trades table - the
        same table the dashboard's Portfolio page Stop Loss column reads
        from - instead of silently dropping this trade's P/L from every
        performance report forever."""
        if self.closed_trade_logger is None or self.recorder is None:
            return
        if exit_price is None:
            logger.warning("Exit for %s: no open-lot state and no exit price "
                           "available; trade not logged.", symbol)
            return
        last_buy = self.recorder.get_last_buy_trade(symbol)
        if last_buy is None:
            logger.warning(
                "Exit for %s: no open-lot state AND no prior buy row in "
                "trades table; entry price is unrecoverable, so this trade's "
                "P/L cannot be added to closed_trades. Exit price was %.2f.",
                symbol, exit_price,
            )
            return
        if self.recorder.has_logged_closed_trade_since(symbol, last_buy.get("ts")):
            logger.info(
                "Exit for %s: already logged via the normal sell path for "
                "this round-trip; skipping to avoid a duplicate closed_trades row.",
                symbol,
            )
            return
        entry_price = last_buy["price"]
        qty = last_buy["qty"]
        full_reason = f"{exit_reason} (reconstructed - open-lot state was lost)"
        pnl = self.closed_trade_logger.log(
            symbol, qty, entry_price, exit_price,
            exit_reason=full_reason, entry_time=None,
        )
        logger.info(
            "Closed trade (reconstructed from trades table - open-lot state "
            "was missing) %s: entry=%.2f exit=%.2f pnl=%.2f",
            symbol, entry_price, exit_price, pnl,
            extra={"symbol": symbol, "decision": "closed_trade", "entry_price": entry_price,
                   "exit_price": exit_price, "pnl": pnl, "exit_reason": exit_reason},
        )
        pnl_pct = ((exit_price - entry_price) / entry_price * 100.0) if entry_price else 0.0
        self.recorder.record_closed_trade(
            symbol=symbol, qty=qty, entry_price=entry_price, exit_price=exit_price,
            pnl=pnl, pnl_pct=pnl_pct, exit_reason=full_reason,
            entry_time=None, buy_reason=last_buy.get("reason"),
            news_summary=last_buy.get("rationale"), sector=last_buy.get("sector"),
            confidence_score=last_buy.get("sentiment_score"),
            confidence_label=last_buy.get("sentiment_label"),
            market_regime=last_buy.get("market_regime"), strategy_version=None,
        )
        self.recorder.record_notification(
            type_="trade_executed", title=f"{symbol} position closed (reconstructed)",
            message=(f"entry={entry_price:.2f} exit={exit_price:.2f} pnl={pnl:.2f} - "
                     f"reconstructed from trade history because open-lot state was lost"),
            severity="info" if pnl >= 0 else "warning",
        )

    def _log_auto_exit(self, symbol: str) -> None:
        """A position vanished since last cycle without us closing it here —
        almost always a bracket stop-loss/take-profit fill. Look up the fill
        and log realized P/L against the open-lot entry we recorded at buy
        time. If we already logged this exit ourselves (see _do_sell), the
        open lot is already gone - `_log_closed_trade_from_history` below
        detects that case (via has_logged_closed_trade_since) and no-ops
        rather than double-logging it. If the open lot is gone because
        bot/state.py's local file was wiped by a redeploy instead, that same
        fallback reconstructs the trade from the trades table so it isn't
        silently lost from closed_trades."""
        if self.closed_trade_logger is None:
            return
        lot = self.state.peek_open(symbol)
        if lot is None:
            fill = self.broker.last_fill(symbol, side="sell")
            exit_price = fill.filled_avg_price if fill else self.broker.latest_price(symbol)
            self._log_closed_trade_from_history(
                symbol, exit_price, "auto_exit (stop_loss_or_take_profit)"
            )
            return
        fill = self.broker.last_fill(symbol, side="sell")
        exit_price = fill.filled_avg_price if fill else self.broker.latest_price(symbol)
        if exit_price is None:
            logger.warning("Could not resolve exit price for auto-exited %s; "
                           "leaving open-lot record for a later cycle.", symbol)
            return
        self.state.pop_open(symbol)
        pnl = self.closed_trade_logger.log(
            symbol, lot["qty"], lot["entry_price"], exit_price,
            exit_reason="auto_exit (stop_loss_or_take_profit)", entry_time=lot.get("entry_time"),
        )
        logger.info("Closed trade (auto-exit) %s: entry=%.2f exit=%.2f pnl=%.2f",
                    symbol, lot["entry_price"], exit_price, pnl,
                    extra={"symbol": symbol, "decision": "closed_trade",
                           "entry_price": lot["entry_price"], "exit_price": exit_price,
                           "pnl": pnl, "exit_reason": "auto_exit"})
        pnl_pct = ((exit_price - lot["entry_price"]) / lot["entry_price"] * 100.0
                   if lot["entry_price"] else 0.0)
        self.recorder.record_closed_trade(
            symbol=symbol, qty=lot["qty"], entry_price=lot["entry_price"], exit_price=exit_price,
            pnl=pnl, pnl_pct=pnl_pct, exit_reason="auto_exit (stop_loss_or_take_profit)",
            entry_time=lot.get("entry_time"), buy_reason=lot.get("reason"),
            news_summary=lot.get("rationale"), sector=lot.get("sector"),
            confidence_score=lot.get("sentiment_score"), confidence_label=lot.get("sentiment_label"),
            market_regime=lot.get("market_regime"), strategy_version=lot.get("strategy_version"),
        )
        self.recorder.record_notification(
            type_="trade_executed", title=f"{symbol} position closed (auto-exit)",
            message=f"entry={lot['entry_price']:.2f} exit={exit_price:.2f} pnl={pnl:.2f}",
            severity="info" if pnl >= 0 else "warning",
        )

    def _process_symbol(self, symbol, acct, exposure, open_positions, pending,
                        sector_counts, new_entries_allowed, stats, market_regime=None) -> float:
        articles = self.news.fetch(
            symbol, self.cfg.news.lookback_hours, self.cfg.news.max_articles_per_symbol
        )
        sentiment = self.analyzer.analyze(symbol, articles)
        stats.evaluated += 1
        logger.info(
            "%s score=%.1f (%s) headlines=%d (+%d/-%d): %s",
            symbol, sentiment.score, sentiment.label, sentiment.article_count,
            sentiment.positive_count, sentiment.negative_count, sentiment.rationale,
            extra={"symbol": symbol, "decision": "scan", "sentiment_score": sentiment.score,
                   "sentiment_label": sentiment.label, "headline_count": sentiment.article_count,
                   "positive_headlines": sentiment.positive_count,
                   "negative_headlines": sentiment.negative_count},
        )
        self.recorder.record_decision(
            symbol=symbol, decision="scan", sentiment_score=sentiment.score,
            sentiment_label=sentiment.label, headline_count=sentiment.article_count,
            positive_headlines=sentiment.positive_count, negative_headlines=sentiment.negative_count,
            rationale=sentiment.rationale,
        )

        # Strategy v2, Path B shadow-position lifecycle: resolve an open
        # shadow signal (if any) BEFORE the holding/not-holding branch below,
        # and regardless of new_entries_allowed/pause - a hypothetical
        # position that's already "open" needs its exit checked every cycle
        # the same way a real mean-reversion holding does, whether or not
        # this cycle would otherwise allow a new entry. Runs whether or not
        # this bot also holds `symbol` for real via Path A; the two are
        # tracked independently. Cheap no-op (one has_open check, no market
        # data call) for the overwhelming majority of symbols that have no
        # open shadow position.
        if self.cfg.strategy.reversion_enabled and not self.cfg.strategy.reversion_live:
            self._maybe_resolve_shadow_position(symbol)

        holding = symbol in open_positions and open_positions[symbol] != 0

        # ---- SELL / holding management ----
        # Strategy v2: a position's exit rule depends on which leg opened it
        # (see bot/state.py's record_open). Mean-reversion entries exit on
        # their own RSI/max-hold rule, not the sentiment-sell rule below -
        # they were never entered on sentiment in the first place. Missing
        # open-lot data (state file lost, or a position predating v2) falls
        # back to 'sentiment_momentum', i.e. today's existing behavior.
        if holding:
            lot = self.state.peek_open(symbol)
            entry_path = (lot or {}).get("entry_path") or "sentiment_momentum"

            if entry_path == "mean_reversion":
                snap = self.broker.market_snapshot(
                    symbol, sma_period=self.cfg.strategy.sma_period,
                    volume_lookback_days=self.cfg.strategy.volume_lookback_days,
                    session_tz=self.cfg.schedule.market_timezone,
                    rsi_period=self.cfg.strategy.rsi_period,
                )
                exit_reason = self._reversion_exit_reason(snap, lot)
                if exit_reason:
                    self._do_sell(symbol, sentiment, reason=exit_reason, entry_path="mean_reversion")
                    self.state.mark_exit(symbol)
                    sector_counts[sector_of(symbol)] -= 1
                    stats.sells += 1
                return exposure

            # ---- SELL (sentiment leg) - sentiment-momentum positions ----
            if sentiment.score < self.cfg.strategy.sell_threshold:
                if sentiment.article_count < self.cfg.strategy.sell_min_headlines:
                    logger.info("SELL %s skipped: only %d headlines (< %d); leaving price "
                                "bracket to manage it", symbol, sentiment.article_count,
                                self.cfg.strategy.sell_min_headlines,
                                extra={"symbol": symbol, "decision": "sell_skipped",
                                       "reason": "too_few_headlines"})
                    self.recorder.record_decision(
                        symbol=symbol, decision="sell_skipped", reason="too_few_headlines",
                        sentiment_score=sentiment.score, sentiment_label=sentiment.label,
                        headline_count=sentiment.article_count,
                    )
                    return exposure
                self._do_sell(symbol, sentiment,
                              reason=f"sentiment {sentiment.score:.1f} < {self.cfg.strategy.sell_threshold}",
                              entry_path="sentiment_momentum")
                self.state.mark_exit(symbol)
                sector_counts[sector_of(symbol)] -= 1
                stats.sells += 1
            return exposure

        # ---- BUY gates (cheap checks first, API calls last) - shared by both paths ----
        if not new_entries_allowed:
            return exposure
        if symbol in pending:
            logger.info("BUY %s skipped: an order is already pending", symbol,
                       extra={"symbol": symbol, "decision": "buy_skipped", "reason": "order_pending"})
            self.recorder.record_decision(symbol=symbol, decision="buy_skipped", reason="order_pending",
                                          sentiment_score=sentiment.score, sentiment_label=sentiment.label)
            return exposure
        if self.state.in_cooldown(symbol, self.cfg.risk.reentry_cooldown_hours):
            logger.info("BUY %s skipped: in re-entry cooldown", symbol,
                       extra={"symbol": symbol, "decision": "buy_skipped", "reason": "cooldown"})
            self.recorder.record_decision(symbol=symbol, decision="buy_skipped", reason="cooldown",
                                          sentiment_score=sentiment.score, sentiment_label=sentiment.label)
            return exposure
        if stats.buys >= self.cfg.risk.max_new_positions_per_cycle:
            logger.info("BUY %s skipped: per-cycle new-position cap reached (%d)",
                        symbol, self.cfg.risk.max_new_positions_per_cycle,
                        extra={"symbol": symbol, "decision": "buy_skipped",
                               "reason": "per_cycle_cap"})
            self.recorder.record_decision(symbol=symbol, decision="buy_skipped", reason="per_cycle_cap",
                                          sentiment_score=sentiment.score, sentiment_label=sentiment.label)
            return exposure
        # Sector cap (skip the 'unknown' bucket so a custom universe isn't blocked).
        sector = sector_of(symbol)
        if (self.cfg.risk.max_positions_per_sector > 0 and sector != "unknown"
                and sector_counts[sector] >= self.cfg.risk.max_positions_per_sector):
            logger.info("BUY %s skipped: sector '%s' already at cap (%d)",
                        symbol, sector, self.cfg.risk.max_positions_per_sector,
                        extra={"symbol": symbol, "decision": "buy_skipped",
                               "reason": "sector_cap", "sector": sector})
            self.recorder.record_decision(symbol=symbol, decision="buy_skipped", reason="sector_cap",
                                          sentiment_score=sentiment.score, sentiment_label=sentiment.label,
                                          extra={"sector": sector})
            return exposure

        # One data call gives price, gap-aware run-up, SMA, volume ratio, and
        # (when the reversion leg is enabled) the long-horizon trend SMA and RSI.
        snap = self.broker.market_snapshot(
            symbol, sma_period=self.cfg.strategy.sma_period,
            volume_lookback_days=self.cfg.strategy.volume_lookback_days,
            session_tz=self.cfg.schedule.market_timezone,
            trend_sma_period=(self.cfg.strategy.reversion_trend_sma_period
                               if self.cfg.strategy.reversion_enabled else None),
            rsi_period=self.cfg.strategy.rsi_period if self.cfg.strategy.reversion_enabled else None,
        )
        if snap is None:
            return exposure

        # ---- Path A: sentiment-momentum, weighted composite score ----
        momentum_reason = self._evaluate_momentum_path(symbol, sentiment, snap)
        if momentum_reason is not None:
            return self._do_buy(symbol, sentiment, snap.last, acct, exposure, open_positions,
                                sector_counts, stats, market_regime,
                                entry_path="sentiment_momentum", reason=momentum_reason)

        # ---- Path B: technical mean-reversion (RSI-2 style), volume-confirmed ----
        if self.cfg.strategy.reversion_enabled:
            reversion_reason = self._evaluate_reversion_path(symbol, sentiment, snap)
            if reversion_reason is not None:
                if self.cfg.strategy.reversion_live:
                    return self._do_buy(symbol, sentiment, snap.last, acct, exposure, open_positions,
                                        sector_counts, stats, market_regime,
                                        entry_path="mean_reversion", reason=reversion_reason)
                # Shadow mode (default): log what Path B WOULD have bought,
                # place no order. See StrategyConfig.reversion_live. Don't
                # log a second shadow "open" while one is already tracked
                # for this symbol (_maybe_resolve_shadow_position, called
                # earlier this cycle, already re-checked the existing one's
                # exit condition) - otherwise the Shadow vs Live view would
                # see two overlapping open positions for the same symbol.
                if self.recorder.get_open_shadow_position(symbol) is not None:
                    return exposure
                logger.info(
                    "[SHADOW] BUY %s (reversion) would fire: %s - reversion_live=False, "
                    "no order placed", symbol, reversion_reason,
                    extra={"symbol": symbol, "decision": "reversion_shadow_buy",
                           "reason": reversion_reason},
                )
                self.recorder.record_decision(
                    symbol=symbol, decision="reversion_shadow_buy", reason=reversion_reason,
                    sentiment_score=sentiment.score, sentiment_label=sentiment.label,
                    headline_count=sentiment.article_count, price=snap.last,
                    entry_path="mean_reversion",
                )

        return exposure

    def _evaluate_momentum_path(self, symbol, sentiment, snap) -> Optional[str]:
        """Strategy v2, Path A: sentiment-momentum as a weighted composite
        score instead of the old all-or-nothing chain (sentiment>=8 AND
        >=5 headlines AND price>SMA AND volume>=1.5x). A strong signal on
        two legs can now compensate a merely-adequate third leg, instead of
        one weak leg silently killing an otherwise good setup.

        Runup and the short-term SMA trend check stay hard gates - those are
        genuine risk conditions ("already priced in" / "not even in a
        short-term uptrend"), not degrees of quality that should earn
        partial credit.

        Returns a human-readable reason if the composite score clears
        STRATEGY_MOMENTUM_BUY_SCORE, else None."""
        cfg = self.cfg.strategy

        runup = snap.change_pct
        if runup is not None and runup > cfg.max_intraday_runup_pct:
            logger.info("BUY %s (momentum) skipped: already up %.1f%% since prev close (> %.1f%%); "
                        "news likely priced in", symbol, runup, cfg.max_intraday_runup_pct,
                        extra={"symbol": symbol, "decision": "buy_skipped",
                               "reason": "runup", "change_pct": runup})
            self.recorder.record_decision(symbol=symbol, decision="buy_skipped", reason="runup",
                                          sentiment_score=sentiment.score, sentiment_label=sentiment.label,
                                          change_pct=runup, entry_path="sentiment_momentum")
            return None

        if cfg.require_price_above_sma:
            if snap.sma is None:
                logger.info("BUY %s (momentum) skipped: %d-day SMA unavailable (fail-closed)",
                            symbol, cfg.sma_period,
                            extra={"symbol": symbol, "decision": "buy_skipped",
                                   "reason": "sma_unavailable"})
                self.recorder.record_decision(symbol=symbol, decision="buy_skipped",
                                              reason="sma_unavailable", sentiment_score=sentiment.score,
                                              sentiment_label=sentiment.label,
                                              entry_path="sentiment_momentum")
                return None
            if snap.last <= snap.sma:
                logger.info("BUY %s (momentum) skipped: price %.2f not above %d-day SMA %.2f",
                            symbol, snap.last, cfg.sma_period, snap.sma,
                            extra={"symbol": symbol, "decision": "buy_skipped",
                                   "reason": "below_sma", "price": snap.last, "sma": snap.sma})
                self.recorder.record_decision(symbol=symbol, decision="buy_skipped", reason="below_sma",
                                              sentiment_score=sentiment.score, sentiment_label=sentiment.label,
                                              price=snap.last, sma=snap.sma,
                                              entry_path="sentiment_momentum")
                return None

        # Weighted composite - partial credit instead of a hard cutoff on
        # headline count / volume ratio. Sentiment only ever contributes
        # positively (negative/neutral sentiment scores 0 toward a buy).
        sentiment_component = max(0.0, min(sentiment.score, 10.0)) / 10.0
        headline_component = (
            min(sentiment.article_count / cfg.min_headlines, 1.0) if cfg.min_headlines > 0 else 1.0
        )
        if cfg.min_volume_ratio <= 0:
            volume_component = 1.0  # gate disabled
        elif snap.volume_ratio is None:
            volume_component = 0.0  # unconfirmable -> no credit (fail-closed, same spirit as before)
        else:
            volume_component = min(snap.volume_ratio / cfg.min_volume_ratio, 1.0)

        score = (cfg.sentiment_weight * sentiment_component
                 + cfg.headline_weight * headline_component
                 + cfg.volume_weight * volume_component)

        if score < cfg.momentum_buy_score:
            logger.info(
                "BUY %s (momentum) skipped: composite score %.2f < %.2f "
                "(sentiment %.2f x%.1f + headlines %.2f x%.1f + volume %.2f x%.1f)",
                symbol, score, cfg.momentum_buy_score,
                sentiment_component, cfg.sentiment_weight, headline_component, cfg.headline_weight,
                volume_component, cfg.volume_weight,
                extra={"symbol": symbol, "decision": "buy_skipped", "reason": "low_composite_score",
                       "composite_score": score},
            )
            self.recorder.record_decision(
                symbol=symbol, decision="buy_skipped", reason="low_composite_score",
                sentiment_score=sentiment.score, sentiment_label=sentiment.label,
                headline_count=sentiment.article_count, volume_ratio=snap.volume_ratio,
                extra={"composite_score": score}, entry_path="sentiment_momentum",
            )
            return None

        volume_txt = f"{snap.volume_ratio:.2f}x" if snap.volume_ratio is not None else "n/a"
        return (f"composite score {score:.2f} >= {cfg.momentum_buy_score:.2f} "
                f"(sentiment {sentiment.score:.1f}, {sentiment.article_count} headlines, "
                f"volume {volume_txt})")

    def _evaluate_reversion_path(self, symbol, sentiment, snap) -> Optional[str]:
        """Strategy v2, Path B: short-term mean-reversion (Larry Connors
        RSI(2)-style). Buys an oversold dip WITHIN an established long-term
        uptrend, confirmed by volume, vetoed if sentiment is actively
        bearish (news likely explains the drop, not a technical dip worth
        buying). All hard gates - this is a binary "does this specific dip
        qualify" signal, not a scored one, since a dip either is or isn't
        confirmed.

        Returns a human-readable reason if it fires, else None (silently -
        "not oversold" isn't worth a skip-log every cycle for every symbol,
        unlike the other gates below which only get reached once RSI *is*
        oversold and are worth recording when they block an entry)."""
        cfg = self.cfg.strategy

        if snap.trend_sma is None:
            logger.info("BUY %s (reversion) skipped: %d-day trend SMA unavailable (fail-closed)",
                        symbol, cfg.reversion_trend_sma_period,
                        extra={"symbol": symbol, "decision": "reversion_skipped",
                               "reason": "trend_sma_unavailable"})
            return None
        if snap.last <= snap.trend_sma:
            return None  # not in a long-term uptrend - routine, not worth logging every cycle
        if snap.rsi is None:
            logger.info("BUY %s (reversion) skipped: %d-period RSI unavailable (fail-closed)",
                        symbol, cfg.rsi_period,
                        extra={"symbol": symbol, "decision": "reversion_skipped",
                               "reason": "rsi_unavailable"})
            return None
        if snap.rsi > cfg.rsi_oversold:
            return None  # not oversold - no signal, routine

        if cfg.reversion_min_volume_ratio > 0 and (
            snap.volume_ratio is None or snap.volume_ratio < cfg.reversion_min_volume_ratio
        ):
            volume_txt = f"{snap.volume_ratio:.2f}x" if snap.volume_ratio is not None else "unavailable"
            logger.info(
                "BUY %s (reversion) skipped: RSI(%d)=%.1f is oversold but volume ratio %s < "
                "required %.2fx (reversion needs volume confirmation)",
                symbol, cfg.rsi_period, snap.rsi, volume_txt, cfg.reversion_min_volume_ratio,
                extra={"symbol": symbol, "decision": "reversion_skipped", "reason": "low_volume"},
            )
            self.recorder.record_decision(
                symbol=symbol, decision="reversion_skipped", reason="low_volume",
                sentiment_score=sentiment.score, sentiment_label=sentiment.label,
                volume_ratio=snap.volume_ratio, entry_path="mean_reversion",
            )
            return None

        if sentiment.score <= cfg.reversion_sentiment_veto:
            logger.info(
                "BUY %s (reversion) skipped: RSI(%d)=%.1f is oversold but sentiment %.1f <= "
                "%.1f veto threshold (news likely explains the drop)",
                symbol, cfg.rsi_period, snap.rsi, sentiment.score, cfg.reversion_sentiment_veto,
                extra={"symbol": symbol, "decision": "reversion_skipped", "reason": "sentiment_veto"},
            )
            self.recorder.record_decision(
                symbol=symbol, decision="reversion_skipped", reason="sentiment_veto",
                sentiment_score=sentiment.score, sentiment_label=sentiment.label,
                entry_path="mean_reversion",
            )
            return None

        volume_txt = f"{snap.volume_ratio:.2f}x" if snap.volume_ratio is not None else "n/a"
        return (f"RSI({cfg.rsi_period})={snap.rsi:.1f} <= {cfg.rsi_oversold:.0f} oversold, "
                f"price {snap.last:.2f} > {cfg.reversion_trend_sma_period}-day SMA {snap.trend_sma:.2f}, "
                f"volume {volume_txt}, sentiment {sentiment.score:.1f} (no veto)")

    def _reversion_exit_reason(self, snap, lot) -> Optional[str]:
        """Exit rule for a Path B (mean-reversion) position: RSI climbing
        back out of oversold (the Connors-style profit-taking exit), or a
        max hold time so a dip that never reverts doesn't just sit there
        indefinitely - the -10%/+20% bracket is still the safety net under
        both. `snap` may be None (fetch failed - fail-closed, keep holding,
        no exit signal); `lot` may be None (open-lot state lost - skip the
        hold-time check, RSI-exit still applies if snap is available)."""
        cfg = self.cfg.strategy
        if snap is not None and snap.rsi is not None and snap.rsi >= cfg.rsi_exit:
            return f"RSI({cfg.rsi_period})={snap.rsi:.1f} >= {cfg.rsi_exit:.0f} exit threshold"
        entry_time = (lot or {}).get("entry_time")
        if entry_time:
            held_days = (time.time() - entry_time) / 86400.0
            if held_days >= cfg.reversion_max_hold_days:
                return f"max hold of {cfg.reversion_max_hold_days} day(s) reached ({held_days:.1f}d)"
        return None

    def _maybe_resolve_shadow_position(self, symbol: str) -> None:
        """Strategy v2, Path B, shadow mode: if a hypothetical reversion
        position is currently "open" for `symbol` (a 'reversion_shadow_buy'
        decision with no later 'reversion_shadow_exit' - see
        Recorder.get_open_shadow_position), re-check the same RSI-recovery/
        max-hold exit rule a REAL mean-reversion holding uses, and log a
        'reversion_shadow_exit' decision with the simulated P/L if it would
        have fired.

        This is what gives the dashboard's Shadow vs Live comparison durable,
        resolved round-trips to analyze instead of an ever-growing pile of
        re-firing 'would buy' signals for positions that, in reality, would
        eventually have been closed one way or another. Entirely
        observational: never touches open_positions, exposure, risk, or any
        real order - a failure here (DB unreachable, snapshot fetch fails)
        just means this symbol's shadow exit gets re-checked next cycle,
        same fail-open posture as every other best-effort recorder path in
        this module."""
        try:
            open_shadow = self.recorder.get_open_shadow_position(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Shadow-position lookup failed for %s: %s", symbol, exc)
            return
        if open_shadow is None:
            return

        snap = self.broker.market_snapshot(
            symbol, sma_period=self.cfg.strategy.sma_period,
            volume_lookback_days=self.cfg.strategy.volume_lookback_days,
            session_tz=self.cfg.schedule.market_timezone,
            rsi_period=self.cfg.strategy.rsi_period,
        )
        entry_ts = open_shadow.get("ts")
        entry_epoch = entry_ts.timestamp() if entry_ts is not None else None
        lot = {"entry_time": entry_epoch}
        exit_reason = self._reversion_exit_reason(snap, lot)
        if not exit_reason:
            return

        entry_price = open_shadow.get("price")
        exit_price = snap.last if snap is not None else None
        if entry_price is None or exit_price is None:
            logger.warning(
                "Shadow exit for %s fired (%s) but entry or exit price is missing "
                "(entry=%s, exit=%s); leaving the position open for a later cycle "
                "rather than logging an exit with no P/L.",
                symbol, exit_reason, entry_price, exit_price,
            )
            return
        entry_price = float(entry_price)
        held_days = (time.time() - entry_epoch) / 86400.0 if entry_epoch else None
        pnl_pct = (exit_price - entry_price) / entry_price * 100.0 if entry_price else 0.0

        logger.info(
            "[SHADOW] CLOSE %s (reversion): %s - entry=%.2f exit=%.2f pnl_pct=%.2f%%",
            symbol, exit_reason, entry_price, exit_price, pnl_pct,
            extra={"symbol": symbol, "decision": "reversion_shadow_exit", "reason": exit_reason,
                   "entry_price": entry_price, "exit_price": exit_price, "pnl_pct": pnl_pct},
        )
        self.recorder.record_decision(
            symbol=symbol, decision="reversion_shadow_exit", reason=exit_reason,
            price=exit_price, entry_path="mean_reversion",
            extra={
                "entry_price": entry_price, "exit_price": exit_price, "pnl_pct": pnl_pct,
                "held_days": held_days,
            },
        )

    def _maybe_notify_shadow_verdict_ready(self) -> None:
        """One-time alert the moment Path B's shadow sample crosses the same
        rule-of-thumb readiness bar the dashboard's Shadow vs Live page shows
        (SHADOW_VERDICT_MIN_CLOSED closed round-trips and
        SHADOW_VERDICT_MIN_WEEKS weeks observed, module-level above) - so
        there's no need to remember to go check that page; the news comes to
        you (Telegram, if configured - see bot/notifications/, or the
        dashboard's Notifications Centre either way).

        Only meaningful while Path B is still shadow-only; skipped entirely
        once it's live. Fires at most once ever, via Recorder.has_ever_notified's
        durable DB-backed guard - deliberately NOT the in-memory
        one-per-day-style guards this class uses elsewhere
        (_daily_loss_notified_date etc.), since those reset on every Railway
        redeploy and would re-announce "ready" indefinitely once the
        threshold is first crossed. Fails silent on any lookup error, same
        posture as every other best-effort Recorder integration point in
        this module - the next cycle just tries again."""
        if not (self.cfg.strategy.reversion_enabled and not self.cfg.strategy.reversion_live):
            return
        try:
            if self.recorder.has_ever_notified("shadow_verdict_ready"):
                return
            progress = self.recorder.get_shadow_verdict_progress()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Shadow-verdict-readiness check failed: %s", exc)
            return
        if not progress or not progress.get("since_ts"):
            return
        closed_count = progress["closed_count"]
        weeks_observed = (
            (datetime.now(timezone.utc) - progress["since_ts"]).total_seconds() / (7 * 86400.0)
        )
        if closed_count < SHADOW_VERDICT_MIN_CLOSED or weeks_observed < SHADOW_VERDICT_MIN_WEEKS:
            return
        self.recorder.record_notification(
            type_="shadow_verdict_ready", severity="info",
            title="Path B shadow data ready for a verdict",
            message=(
                f"{closed_count} closed shadow round-trips over {weeks_observed:.1f} weeks - "
                "enough to start forming a view on Path B (mean-reversion). "
                "Check the Shadow vs Live page on the dashboard for the full breakdown."
            ),
            metadata={"closed_count": closed_count, "weeks_observed": round(weeks_observed, 1)},
        )

    def _do_buy(self, symbol, sentiment, price, acct, exposure, open_positions,
                sector_counts, stats, market_regime=None,
                entry_path: str = "sentiment_momentum", reason: Optional[str] = None) -> float:
        if price is None or price <= 0:
            return exposure

        decision = self.risk.evaluate(
            symbol, price, acct["portfolio_value"], acct["buying_power"],
            exposure, open_positions,
        )
        if not decision.approved:
            logger.info("BUY %s blocked by risk: %s", symbol, decision.reason,
                       extra={"symbol": symbol, "decision": "buy_blocked", "reason": decision.reason})
            self.recorder.record_decision(symbol=symbol, decision="buy_blocked",
                                          reason=decision.reason, sentiment_score=sentiment.score,
                                          sentiment_label=sentiment.label, entry_path=entry_path)
            stats.blocked += 1
            return exposure

        plan = decision.plan
        if reason is None:
            # Fallback for any future caller that doesn't pass one - matches
            # the pre-v2 message shape.
            reason = (f"score {sentiment.score:.1f} >= {self.cfg.strategy.buy_threshold}, "
                      f"{sentiment.article_count} headlines")
        tagged_reason = f"[{entry_path}] {reason}"

        if self.cfg.risk.dry_run:
            logger.info("[DRY RUN] would BUY %d %s @ ~%.2f (stop %.2f / tp %.2f) via %s",
                        plan.qty, symbol, plan.price, plan.stop_price, plan.take_profit_price,
                        entry_path,
                        extra={"symbol": symbol, "decision": "buy", "dry_run": True,
                               "qty": plan.qty, "price": plan.price, "notional": plan.notional,
                               "entry_path": entry_path})
            self.trade_logger.log("buy", symbol, plan.qty, plan.price, plan.notional,
                                  sentiment, plan.stop_price, plan.take_profit_price,
                                  tagged_reason, dry_run=True, order_id="", status="dry_run",
                                  entry_path=entry_path)
            self.recorder.record_trade(
                action="buy", symbol=symbol, qty=plan.qty, price=plan.price, notional=plan.notional,
                sentiment=sentiment, stop_price=plan.stop_price, take_profit=plan.take_profit_price,
                reason=tagged_reason, rationale=sentiment.rationale, dry_run=True, order_id="",
                status="dry_run", sector=sector_of(symbol), market_regime=market_regime,
                entry_path=entry_path,
            )
            self.recorder.record_decision(
                symbol=symbol, decision="buy", reason=tagged_reason, sentiment_score=sentiment.score,
                sentiment_label=sentiment.label, headline_count=sentiment.article_count,
                rationale=sentiment.rationale, price=plan.price, extra={"dry_run": True},
                entry_path=entry_path,
            )
        else:
            # Idempotency key stable within a 30-min slot, so a crash/restart or a
            # second instance can't open a duplicate of the same intended entry.
            slot = datetime.now(timezone.utc).strftime("%Y%m%d%H") + (
                "00" if datetime.now(timezone.utc).minute < 30 else "30")
            try:
                order = self.broker.submit_bracket_buy(
                    symbol, plan.qty, plan.stop_price, plan.take_profit_price,
                    client_order_id=f"{symbol}-{slot}",
                )
            except Exception as exc:  # noqa: BLE001 - order failed even after retries
                logger.error("BUY %s failed after retries: %s", symbol, exc,
                            extra={"symbol": symbol, "decision": "buy_failed", "error": str(exc)})
                self.recorder.record_decision(symbol=symbol, decision="buy_failed",
                                              reason=str(exc), sentiment_score=sentiment.score,
                                              sentiment_label=sentiment.label, entry_path=entry_path)
                self.recorder.record_notification(
                    type_="error", severity="warning", title=f"BUY {symbol} failed",
                    message=str(exc),
                )
                return exposure
            logger.info("Submitted BUY %s: order %s status %s via %s",
                        symbol, order.order_id, order.status, entry_path,
                        extra={"symbol": symbol, "decision": "buy", "dry_run": False,
                               "qty": plan.qty, "price": plan.price, "notional": plan.notional,
                               "order_id": order.order_id, "status": order.status,
                               "entry_path": entry_path})
            self.trade_logger.log("buy", symbol, plan.qty, plan.price, plan.notional,
                                  sentiment, plan.stop_price, plan.take_profit_price,
                                  tagged_reason, dry_run=False, order_id=order.order_id,
                                  status=order.status, entry_path=entry_path)
            self.recorder.record_trade(
                action="buy", symbol=symbol, qty=plan.qty, price=plan.price, notional=plan.notional,
                sentiment=sentiment, stop_price=plan.stop_price, take_profit=plan.take_profit_price,
                reason=tagged_reason, rationale=sentiment.rationale, dry_run=False,
                order_id=order.order_id, status=order.status,
                sector=sector_of(symbol), market_regime=market_regime, entry_path=entry_path,
            )
            self.recorder.record_decision(
                symbol=symbol, decision="buy", reason=tagged_reason, sentiment_score=sentiment.score,
                sentiment_label=sentiment.label, headline_count=sentiment.article_count,
                rationale=sentiment.rationale, price=plan.price,
                extra={"dry_run": False, "order_id": order.order_id, "status": order.status},
                entry_path=entry_path,
            )
            self.recorder.record_notification(
                type_="trade_executed", title=f"BUY {symbol}",
                message=f"{plan.qty} shares @ ~{plan.price:.2f} ({tagged_reason})",
            )

        # Remember what we paid (real or simulated) so the performance report
        # can compute P/L whenever this position eventually closes. Also keep
        # the reason/confidence and entry_path so the dashboard can show why
        # we bought it, and so the exit logic above knows which rule to use.
        self.state.record_open(symbol, plan.price, plan.qty, reason=tagged_reason,
                               sentiment_score=sentiment.score, sentiment_label=sentiment.label,
                               rationale=sentiment.rationale, sector=sector_of(symbol),
                               market_regime=market_regime,
                               strategy_version=self.version_provider.current_version(),
                               entry_path=entry_path)

        open_positions[symbol] = plan.qty
        sector_counts[sector_of(symbol)] += 1
        stats.buys += 1
        return exposure + plan.notional

    def _do_sell(self, symbol, sentiment, reason, entry_path: str = "sentiment_momentum") -> None:
        if self.cfg.risk.dry_run:
            exit_price = self.broker.latest_price(symbol)
            logger.info("[DRY RUN] would CLOSE %s (%s)", symbol, reason,
                       extra={"symbol": symbol, "decision": "sell", "dry_run": True,
                              "reason": reason})
            self.trade_logger.log("sell", symbol, 0, 0.0, 0.0, sentiment, 0.0, 0.0,
                                  reason, dry_run=True, order_id="", status="dry_run",
                                  entry_path=entry_path)
            self.recorder.record_trade(
                action="sell", symbol=symbol, qty=0, price=0.0, notional=0.0, sentiment=sentiment,
                stop_price=0.0, take_profit=0.0, reason=reason, rationale=sentiment.rationale,
                dry_run=True, order_id="", status="dry_run", sector=sector_of(symbol),
                entry_path=entry_path,
            )
            self.recorder.record_decision(
                symbol=symbol, decision="sell", reason=reason, sentiment_score=sentiment.score,
                sentiment_label=sentiment.label, headline_count=sentiment.article_count,
                rationale=sentiment.rationale, price=exit_price, extra={"dry_run": True},
            )
            if self.closed_trade_logger is not None and exit_price is not None:
                lot = self.state.pop_open(symbol)
                if lot is not None:
                    pnl = self.closed_trade_logger.log(
                        symbol, lot["qty"], lot["entry_price"], exit_price,
                        exit_reason=f"dry_run: {reason}", entry_time=lot.get("entry_time"),
                    )
                    logger.info("Closed trade (dry-run) %s: pnl=%.2f", symbol, pnl,
                               extra={"symbol": symbol, "decision": "closed_trade",
                                      "pnl": pnl, "dry_run": True})
                    pnl_pct = ((exit_price - lot["entry_price"]) / lot["entry_price"] * 100.0
                               if lot["entry_price"] else 0.0)
                    self.recorder.record_closed_trade(
                        symbol=symbol, qty=lot["qty"], entry_price=lot["entry_price"],
                        exit_price=exit_price, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason=f"dry_run: {reason}", entry_time=lot.get("entry_time"),
                        buy_reason=lot.get("reason"), news_summary=lot.get("rationale"),
                        sector=lot.get("sector"), confidence_score=lot.get("sentiment_score"),
                        confidence_label=lot.get("sentiment_label"),
                        market_regime=lot.get("market_regime"),
                        strategy_version=lot.get("strategy_version"),
                    )
                else:
                    # Same open-lot-loss scenario documented on
                    # _log_closed_trade_from_history - reconstruct from the
                    # trades table instead of dropping this exit.
                    self._log_closed_trade_from_history(symbol, exit_price, f"dry_run: {reason}")
            return

        # Guard against racing a working order for the same symbol - most
        # commonly _adopt_legacy_positions placing a protective stop/take-
        # profit in THIS SAME cycle (its qty check runs before this sentiment
        # sell does, using a pending-orders snapshot taken before adoption
        # ran, so it doesn't know a new order now holds the shares). Without
        # this, close_position's own cancel-then-close attempts to flatten a
        # position whose shares are already committed to that other order,
        # fails with Alpaca's "insufficient qty available", and fires a
        # scary but harmless "SELL failed" alert - the position is usually
        # already being closed by the order that beat it here. Checking
        # fresh (not the cycle-start `pending` set) since that's exactly
        # what's stale in this scenario.
        if symbol in self.broker.pending_order_symbols():
            logger.info(
                "SELL %s skipped: a working order already exists for this symbol (likely "
                "today's protective stop/take-profit) - avoiding a duplicate close_position "
                "call. If sentiment is still bearish next cycle and that order hasn't "
                "resolved it yet, this will retry then.",
                symbol, extra={"symbol": symbol, "decision": "sell_skipped", "reason": "already_closing"},
            )
            self.recorder.record_decision(
                symbol=symbol, decision="sell_skipped", reason="already_closing",
                sentiment_score=sentiment.score, sentiment_label=sentiment.label,
            )
            return

        order = self.broker.close_position(symbol)
        if order is None:
            logger.error("SELL %s failed (close_position returned no order)", symbol,
                        extra={"symbol": symbol, "decision": "sell_failed"})
            self.recorder.record_notification(
                type_="error", severity="warning", title=f"SELL {symbol} failed",
                message="close_position returned no order",
            )
            return
        logger.info("Submitted CLOSE %s: order %s (%s)", symbol, order.order_id, reason,
                    extra={"symbol": symbol, "decision": "sell", "dry_run": False,
                           "order_id": order.order_id, "reason": reason})
        self.trade_logger.log("sell", symbol, order.qty, 0.0, 0.0, sentiment, 0.0, 0.0,
                              reason, dry_run=False, order_id=order.order_id,
                              status=order.status, entry_path=entry_path)
        self.recorder.record_trade(
            action="sell", symbol=symbol, qty=order.qty, price=0.0, notional=0.0, sentiment=sentiment,
            stop_price=0.0, take_profit=0.0, reason=reason, rationale=sentiment.rationale,
            dry_run=False, order_id=order.order_id, status=order.status, sector=sector_of(symbol),
            entry_path=entry_path,
        )
        self.recorder.record_decision(
            symbol=symbol, decision="sell", reason=reason, sentiment_score=sentiment.score,
            sentiment_label=sentiment.label, headline_count=sentiment.article_count,
            rationale=sentiment.rationale,
            extra={"dry_run": False, "order_id": order.order_id, "status": order.status},
        )
        self.recorder.record_notification(
            type_="trade_executed", title=f"SELL {symbol}", message=reason,
        )

        if self.closed_trade_logger is not None:
            exit_price = order.filled_avg_price or self.broker.latest_price(symbol)
            lot = self.state.pop_open(symbol)
            if lot is not None and exit_price is not None:
                pnl = self.closed_trade_logger.log(
                    symbol, lot["qty"], lot["entry_price"], exit_price,
                    exit_reason=reason, entry_time=lot.get("entry_time"),
                )
                pnl_pct = ((exit_price - lot["entry_price"]) / lot["entry_price"] * 100.0
                           if lot["entry_price"] else 0.0)
                self.recorder.record_closed_trade(
                    symbol=symbol, qty=lot["qty"], entry_price=lot["entry_price"],
                    exit_price=exit_price, pnl=pnl, pnl_pct=pnl_pct, exit_reason=reason,
                    entry_time=lot.get("entry_time"), buy_reason=lot.get("reason"),
                    news_summary=lot.get("rationale"), sector=lot.get("sector"),
                    confidence_score=lot.get("sentiment_score"),
                    confidence_label=lot.get("sentiment_label"),
                    market_regime=lot.get("market_regime"),
                    strategy_version=lot.get("strategy_version"),
                )
                logger.info("Closed trade %s: entry=%.2f exit=%.2f pnl=%.2f",
                            symbol, lot["entry_price"], exit_price, pnl,
                            extra={"symbol": symbol, "decision": "closed_trade",
                                   "entry_price": lot["entry_price"], "exit_price": exit_price,
                                   "pnl": pnl})
            elif exit_price is not None:
                # Same open-lot-loss scenario documented on
                # _log_closed_trade_from_history - reconstruct from the
                # trades table instead of dropping this exit.
                self._log_closed_trade_from_history(symbol, exit_price, reason)
