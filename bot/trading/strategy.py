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
  * today's volume >= min_volume_ratio x its volume_lookback_days-day average
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

from ..universe.static_universe import sector_of

logger = logging.getLogger(__name__)


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
    # Placeholder until Phase 2 (Strategy Version Management) exists - every
    # trade entered so far is tagged with this version so Strategy
    # Intelligence has something to attribute performance to from day one.
    # Phase 2 replaces this with a real strategy_versions-table lookup.
    CURRENT_STRATEGY_VERSION = "v1"

    def __init__(self, cfg, broker, universe, news, analyzer, risk,
                 trade_logger, summary_logger, state, closed_trade_logger=None,
                 recorder=None):
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
        # Guards the daily-loss-limit notification so it fires once per
        # breach-day, not every 30-min cycle for the rest of the day.
        self._daily_loss_notified_date: Optional[str] = None
        # Same one-per-day guard for the PDT warning (see _maybe_notify_pdt).
        self._pdt_notified_date: Optional[str] = None

    def run_cycle(self, force: bool = False, scheduler_status: str = "scheduled") -> None:
        if not force and not self.broker.is_market_open():
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

        exited = self.state.detect_exits(list(open_positions.keys()))
        if exited:
            logger.info("Detected exits since last cycle (cooldown started): %s",
                        ", ".join(exited))
            for sym in exited:
                self._log_auto_exit(sym)

        new_entries_allowed = not self.risk.daily_loss_breached(
            acct["equity"], acct["last_equity"]
        )
        if not new_entries_allowed:
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

    def _sync_open_positions_snapshot(self, portfolio_value) -> None:
        """Push the full current book (with unrealized P/L straight from
        Alpaca, plus the entry reason/confidence we recorded at buy time) to
        the dashboard's open_positions table. Best-effort: any failure here
        never affects trading, only what the dashboard shows."""
        try:
            detailed = self.broker.open_positions_detailed()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch detailed positions for dashboard sync: %s", exc)
            return
        rows = []
        for symbol, pos in detailed.items():
            lot = self.state.peek_open(symbol) or {}
            allocation_pct = (
                (pos.market_value / portfolio_value * 100.0)
                if portfolio_value else None
            )
            rows.append({
                "symbol": symbol, "qty": pos.qty, "avg_entry_price": pos.avg_entry_price,
                "current_price": pos.current_price, "market_value": pos.market_value,
                "unrealized_pl": pos.unrealized_pl, "unrealized_plpc": pos.unrealized_plpc,
                "allocation_pct": allocation_pct, "ai_confidence": lot.get("sentiment_score"),
                "entry_reason": lot.get("reason"), "entry_time": lot.get("entry_time"),
            })
        self.recorder.sync_open_positions(rows)

    def _log_auto_exit(self, symbol: str) -> None:
        """A position vanished since last cycle without us closing it here —
        almost always a bracket stop-loss/take-profit fill. Look up the fill
        and log realized P/L against the open-lot entry we recorded at buy
        time. If we already logged this exit ourselves (see _do_sell), the
        open lot is already gone and this is a no-op."""
        if self.closed_trade_logger is None:
            return
        lot = self.state.peek_open(symbol)
        if lot is None:
            return  # already closed out explicitly by _do_sell this run
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

        holding = symbol in open_positions and open_positions[symbol] != 0

        # ---- SELL (sentiment leg) ----
        if holding and sentiment.score < self.cfg.strategy.sell_threshold:
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
                          reason=f"sentiment {sentiment.score:.1f} < {self.cfg.strategy.sell_threshold}")
            self.state.mark_exit(symbol)
            sector_counts[sector_of(symbol)] -= 1
            stats.sells += 1
            return exposure

        # ---- BUY gates (cheap checks first, API calls last) ----
        if holding or not new_entries_allowed:
            return exposure
        if sentiment.score < self.cfg.strategy.buy_threshold:
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
        if sentiment.article_count < self.cfg.strategy.min_headlines:
            logger.info("BUY %s skipped: only %d headlines (< %d)",
                        symbol, sentiment.article_count, self.cfg.strategy.min_headlines,
                        extra={"symbol": symbol, "decision": "buy_skipped",
                               "reason": "too_few_headlines"})
            self.recorder.record_decision(symbol=symbol, decision="buy_skipped",
                                          reason="too_few_headlines", sentiment_score=sentiment.score,
                                          sentiment_label=sentiment.label,
                                          headline_count=sentiment.article_count)
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

        # One data call gives price, gap-aware run-up, SMA, and volume ratio.
        snap = self.broker.market_snapshot(
            symbol, sma_period=self.cfg.strategy.sma_period,
            volume_lookback_days=self.cfg.strategy.volume_lookback_days,
        )
        if snap is None:
            return exposure

        runup = snap.change_pct
        if runup is not None and runup > self.cfg.strategy.max_intraday_runup_pct:
            logger.info("BUY %s skipped: already up %.1f%% since prev close (> %.1f%%); "
                        "news likely priced in", symbol, runup,
                        self.cfg.strategy.max_intraday_runup_pct,
                        extra={"symbol": symbol, "decision": "buy_skipped",
                               "reason": "runup", "change_pct": runup})
            self.recorder.record_decision(symbol=symbol, decision="buy_skipped", reason="runup",
                                          sentiment_score=sentiment.score, sentiment_label=sentiment.label,
                                          change_pct=runup)
            return exposure

        # --- Confirmation filter: price above its N-day SMA ---
        if self.cfg.strategy.require_price_above_sma:
            if snap.sma is None:
                logger.info("BUY %s skipped: %d-day SMA unavailable (fail-closed)",
                            symbol, self.cfg.strategy.sma_period,
                            extra={"symbol": symbol, "decision": "buy_skipped",
                                   "reason": "sma_unavailable"})
                self.recorder.record_decision(symbol=symbol, decision="buy_skipped",
                                              reason="sma_unavailable", sentiment_score=sentiment.score,
                                              sentiment_label=sentiment.label)
                return exposure
            if snap.last <= snap.sma:
                logger.info("BUY %s skipped: price %.2f not above %d-day SMA %.2f",
                            symbol, snap.last, self.cfg.strategy.sma_period, snap.sma,
                            extra={"symbol": symbol, "decision": "buy_skipped",
                                   "reason": "below_sma", "price": snap.last, "sma": snap.sma})
                self.recorder.record_decision(symbol=symbol, decision="buy_skipped", reason="below_sma",
                                              sentiment_score=sentiment.score, sentiment_label=sentiment.label,
                                              price=snap.last, sma=snap.sma)
                return exposure

        # --- Confirmation filter: today's volume >= min_volume_ratio x avg ---
        if self.cfg.strategy.min_volume_ratio > 0:
            if snap.volume_ratio is None:
                logger.info("BUY %s skipped: today's volume not yet confirmable "
                           "vs %d-day average (fail-closed)",
                            symbol, self.cfg.strategy.volume_lookback_days,
                            extra={"symbol": symbol, "decision": "buy_skipped",
                                   "reason": "volume_unavailable"})
                self.recorder.record_decision(symbol=symbol, decision="buy_skipped",
                                              reason="volume_unavailable", sentiment_score=sentiment.score,
                                              sentiment_label=sentiment.label)
                return exposure
            if snap.volume_ratio < self.cfg.strategy.min_volume_ratio:
                logger.info("BUY %s skipped: volume ratio %.2fx < required %.2fx",
                            symbol, snap.volume_ratio, self.cfg.strategy.min_volume_ratio,
                            extra={"symbol": symbol, "decision": "buy_skipped",
                                   "reason": "low_volume", "volume_ratio": snap.volume_ratio})
                self.recorder.record_decision(symbol=symbol, decision="buy_skipped", reason="low_volume",
                                              sentiment_score=sentiment.score, sentiment_label=sentiment.label,
                                              volume_ratio=snap.volume_ratio)
                return exposure

        return self._do_buy(symbol, sentiment, snap.last, acct, exposure,
                            open_positions, sector_counts, stats, market_regime)

    def _do_buy(self, symbol, sentiment, price, acct, exposure, open_positions,
                sector_counts, stats, market_regime=None) -> float:
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
                                          sentiment_label=sentiment.label)
            stats.blocked += 1
            return exposure

        plan = decision.plan
        reason = (f"score {sentiment.score:.1f} >= {self.cfg.strategy.buy_threshold}, "
                  f"{sentiment.article_count} headlines")

        if self.cfg.risk.dry_run:
            logger.info("[DRY RUN] would BUY %d %s @ ~%.2f (stop %.2f / tp %.2f)",
                        plan.qty, symbol, plan.price, plan.stop_price, plan.take_profit_price,
                        extra={"symbol": symbol, "decision": "buy", "dry_run": True,
                               "qty": plan.qty, "price": plan.price, "notional": plan.notional})
            self.trade_logger.log("buy", symbol, plan.qty, plan.price, plan.notional,
                                  sentiment, plan.stop_price, plan.take_profit_price,
                                  reason, dry_run=True, order_id="", status="dry_run")
            self.recorder.record_trade(
                action="buy", symbol=symbol, qty=plan.qty, price=plan.price, notional=plan.notional,
                sentiment=sentiment, stop_price=plan.stop_price, take_profit=plan.take_profit_price,
                reason=reason, rationale=sentiment.rationale, dry_run=True, order_id="", status="dry_run",
                sector=sector_of(symbol), market_regime=market_regime,
            )
            self.recorder.record_decision(
                symbol=symbol, decision="buy", reason=reason, sentiment_score=sentiment.score,
                sentiment_label=sentiment.label, headline_count=sentiment.article_count,
                rationale=sentiment.rationale, price=plan.price, extra={"dry_run": True},
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
                                              sentiment_label=sentiment.label)
                self.recorder.record_notification(
                    type_="error", severity="warning", title=f"BUY {symbol} failed",
                    message=str(exc),
                )
                return exposure
            logger.info("Submitted BUY %s: order %s status %s",
                        symbol, order.order_id, order.status,
                        extra={"symbol": symbol, "decision": "buy", "dry_run": False,
                               "qty": plan.qty, "price": plan.price, "notional": plan.notional,
                               "order_id": order.order_id, "status": order.status})
            self.trade_logger.log("buy", symbol, plan.qty, plan.price, plan.notional,
                                  sentiment, plan.stop_price, plan.take_profit_price,
                                  reason, dry_run=False, order_id=order.order_id,
                                  status=order.status)
            self.recorder.record_trade(
                action="buy", symbol=symbol, qty=plan.qty, price=plan.price, notional=plan.notional,
                sentiment=sentiment, stop_price=plan.stop_price, take_profit=plan.take_profit_price,
                reason=reason, rationale=sentiment.rationale, dry_run=False,
                order_id=order.order_id, status=order.status,
                sector=sector_of(symbol), market_regime=market_regime,
            )
            self.recorder.record_decision(
                symbol=symbol, decision="buy", reason=reason, sentiment_score=sentiment.score,
                sentiment_label=sentiment.label, headline_count=sentiment.article_count,
                rationale=sentiment.rationale, price=plan.price,
                extra={"dry_run": False, "order_id": order.order_id, "status": order.status},
            )
            self.recorder.record_notification(
                type_="trade_executed", title=f"BUY {symbol}",
                message=f"{plan.qty} shares @ ~{plan.price:.2f} ({reason})",
            )

        # Remember what we paid (real or simulated) so the performance report
        # can compute P/L whenever this position eventually closes. Also keep
        # the reason/confidence so the dashboard can show why we bought it.
        self.state.record_open(symbol, plan.price, plan.qty, reason=reason,
                               sentiment_score=sentiment.score, sentiment_label=sentiment.label,
                               rationale=sentiment.rationale, sector=sector_of(symbol),
                               market_regime=market_regime,
                               strategy_version=self.CURRENT_STRATEGY_VERSION)

        open_positions[symbol] = plan.qty
        sector_counts[sector_of(symbol)] += 1
        stats.buys += 1
        return exposure + plan.notional

    def _do_sell(self, symbol, sentiment, reason) -> None:
        if self.cfg.risk.dry_run:
            exit_price = self.broker.latest_price(symbol)
            logger.info("[DRY RUN] would CLOSE %s (%s)", symbol, reason,
                       extra={"symbol": symbol, "decision": "sell", "dry_run": True,
                              "reason": reason})
            self.trade_logger.log("sell", symbol, 0, 0.0, 0.0, sentiment, 0.0, 0.0,
                                  reason, dry_run=True, order_id="", status="dry_run")
            self.recorder.record_trade(
                action="sell", symbol=symbol, qty=0, price=0.0, notional=0.0, sentiment=sentiment,
                stop_price=0.0, take_profit=0.0, reason=reason, rationale=sentiment.rationale,
                dry_run=True, order_id="", status="dry_run", sector=sector_of(symbol),
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
                              status=order.status)
        self.recorder.record_trade(
            action="sell", symbol=symbol, qty=order.qty, price=0.0, notional=0.0, sentiment=sentiment,
            stop_price=0.0, take_profit=0.0, reason=reason, rationale=sentiment.rationale,
            dry_run=False, order_id=order.order_id, status=order.status, sector=sector_of(symbol),
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
