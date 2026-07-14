"""Optional Postgres persistence for the monitoring dashboard.

This module is intentionally decoupled from the trading logic: it only
records what already happened (decisions, trades, snapshots, notifications)
so the dashboard has a database to read from. It never influences a buy,
sell, or risk decision.

Design goals:
  * Zero impact if DATABASE_URL isn't set (e.g. local dev) - every record_*
    call becomes a no-op.
  * Never raise into the caller. A DB hiccup should slow nothing down and
    crash nothing - it just logs a warning and drops that one row, exactly
    like every other best-effort integration in this codebase.
  * One short-lived connection per write. At this bot's write volume (well
    under a hundred rows per 30-minute cycle) that's simpler and safer than
    managing a long-lived pool that Postgres or Railway might silently drop.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class Recorder:
    def __init__(self, database_url: Optional[str] = None):
        self.database_url = database_url or os.environ.get("DATABASE_URL", "")
        self.enabled = bool(self.database_url)
        self._psycopg2 = None
        # Tracks whether the most recent write succeeded, so callers (see
        # bot/notifications/service.py) can detect a DB outage and raise a
        # Telegram-only alert without this module needing to know Telegram
        # exists. Starts True (optimistic) so a never-written Recorder isn't
        # reported as failing.
        self.healthy = True
        self.last_error: Optional[str] = None
        if self.enabled:
            try:
                import psycopg2  # noqa: F401

                self._psycopg2 = psycopg2
            except ImportError:
                logger.warning(
                    "DATABASE_URL is set but psycopg2-binary isn't installed; "
                    "dashboard persistence disabled. Add psycopg2-binary to "
                    "requirements.txt and redeploy."
                )
                self.enabled = False
        else:
            logger.info("DATABASE_URL not set; dashboard persistence disabled "
                        "(bot still runs normally on CSV/JSON logs only).")

    # ---- internals ---------------------------------------------------
    def _execute(self, sql: str, params: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            conn = self._psycopg2.connect(self.database_url, connect_timeout=5)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dashboard DB connect failed (row dropped): %s", exc)
            self.healthy = False
            self.last_error = str(exc)
            return
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
            self.healthy = True
            self.last_error = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dashboard DB write failed (row dropped): %s", exc)
            self.healthy = False
            self.last_error = str(exc)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _json(value: Optional[Dict]) -> Optional[str]:
        return json.dumps(value, default=str) if value is not None else None

    # ---- public API ----------------------------------------------------
    def record_heartbeat(self, *, status: str, scheduler_status: str,
                          market_open: Optional[bool] = None, dry_run: Optional[bool] = None,
                          portfolio_value: Optional[float] = None, cash: Optional[float] = None,
                          equity: Optional[float] = None, buying_power: Optional[float] = None,
                          open_positions: Optional[int] = None, message: Optional[str] = None,
                          api_latency_ms: Optional[float] = None,
                          trading_mode: Optional[str] = None,
                          daytrade_count: Optional[int] = None,
                          pattern_day_trader: Optional[bool] = None,
                          market_regime: Optional[str] = None) -> None:
        self._execute(
            """
            INSERT INTO heartbeats
                (status, scheduler_status, market_open, dry_run, portfolio_value,
                 cash, equity, buying_power, open_positions, message, api_latency_ms,
                 trading_mode, daytrade_count, pattern_day_trader, market_regime)
            VALUES
                (%(status)s, %(scheduler_status)s, %(market_open)s, %(dry_run)s, %(portfolio_value)s,
                 %(cash)s, %(equity)s, %(buying_power)s, %(open_positions)s, %(message)s,
                 %(api_latency_ms)s, %(trading_mode)s, %(daytrade_count)s, %(pattern_day_trader)s,
                 %(market_regime)s)
            """,
            dict(status=status, scheduler_status=scheduler_status, market_open=market_open,
                 dry_run=dry_run, portfolio_value=portfolio_value, cash=cash, equity=equity,
                 buying_power=buying_power, open_positions=open_positions, message=message,
                 api_latency_ms=api_latency_ms, trading_mode=trading_mode,
                 daytrade_count=daytrade_count, pattern_day_trader=pattern_day_trader,
                 market_regime=market_regime),
        )

    def record_config_status(self, *, trading_mode: str, live_confirmed: bool,
                              risk_dry_run: bool, allow_submit: bool,
                              has_paper_keys: bool, has_live_keys: bool,
                              has_telegram: bool, commit_short: Optional[str] = None,
                              environment: Optional[str] = None) -> None:
        """One row per startup - the dashboard's Live Readiness page reads the
        latest row, since presence/absence of env vars can only be observed
        from inside the bot's own process (never the raw secret values)."""
        self._execute(
            """
            INSERT INTO config_status
                (trading_mode, live_confirmed, risk_dry_run, allow_submit,
                 has_paper_keys, has_live_keys, has_telegram, has_database,
                 commit_short, environment)
            VALUES
                (%(trading_mode)s, %(live_confirmed)s, %(risk_dry_run)s, %(allow_submit)s,
                 %(has_paper_keys)s, %(has_live_keys)s, %(has_telegram)s, true,
                 %(commit_short)s, %(environment)s)
            """,
            dict(trading_mode=trading_mode, live_confirmed=live_confirmed,
                 risk_dry_run=risk_dry_run, allow_submit=allow_submit,
                 has_paper_keys=has_paper_keys, has_live_keys=has_live_keys,
                 has_telegram=has_telegram, commit_short=commit_short, environment=environment),
        )

    def record_account_activities(self, rows) -> None:
        """Upsert a batch of Alpaca account activities (dividends, regulatory
        fees, non-resident withholding). Safe to call with an overlapping
        date window every day - ON CONFLICT keeps this idempotent on Alpaca's
        own activity id. `rows` is a list of dicts with the keys used below."""
        if not self.enabled or not rows:
            return
        try:
            conn = self._psycopg2.connect(self.database_url, connect_timeout=5)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dashboard DB connect failed (activities sync dropped): %s", exc)
            self.healthy = False
            self.last_error = str(exc)
            return
        try:
            with conn:
                with conn.cursor() as cur:
                    for r in rows:
                        cur.execute(
                            """
                            INSERT INTO account_activities
                                (activity_id, activity_type, activity_date, symbol, net_amount,
                                 qty, per_share_amount, description, raw)
                            VALUES
                                (%(activity_id)s, %(activity_type)s, %(activity_date)s, %(symbol)s,
                                 %(net_amount)s, %(qty)s, %(per_share_amount)s, %(description)s, %(raw)s)
                            ON CONFLICT (activity_id) DO NOTHING
                            """,
                            dict(
                                activity_id=r["activity_id"], activity_type=r["activity_type"],
                                activity_date=r["activity_date"], symbol=r.get("symbol"),
                                net_amount=r.get("net_amount"), qty=r.get("qty"),
                                per_share_amount=r.get("per_share_amount"),
                                description=r.get("description"), raw=self._json(r.get("raw")),
                            ),
                        )
            self.healthy = True
            self.last_error = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dashboard DB activities sync failed: %s", exc)
            self.healthy = False
            self.last_error = str(exc)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def record_decision(self, *, symbol: str, decision: str, reason: Optional[str] = None,
                         sentiment_score: Optional[float] = None, sentiment_label: Optional[str] = None,
                         headline_count: Optional[int] = None, positive_headlines: Optional[int] = None,
                         negative_headlines: Optional[int] = None, rationale: Optional[str] = None,
                         price: Optional[float] = None, sma: Optional[float] = None,
                         change_pct: Optional[float] = None, volume_ratio: Optional[float] = None,
                         extra: Optional[Dict] = None) -> None:
        self._execute(
            """
            INSERT INTO decisions
                (symbol, decision, reason, sentiment_score, sentiment_label, headline_count,
                 positive_headlines, negative_headlines, rationale, price, sma, change_pct,
                 volume_ratio, extra)
            VALUES
                (%(symbol)s, %(decision)s, %(reason)s, %(sentiment_score)s, %(sentiment_label)s,
                 %(headline_count)s, %(positive_headlines)s, %(negative_headlines)s, %(rationale)s,
                 %(price)s, %(sma)s, %(change_pct)s, %(volume_ratio)s, %(extra)s)
            """,
            dict(symbol=symbol, decision=decision, reason=reason, sentiment_score=sentiment_score,
                 sentiment_label=sentiment_label, headline_count=headline_count,
                 positive_headlines=positive_headlines, negative_headlines=negative_headlines,
                 rationale=rationale, price=price, sma=sma, change_pct=change_pct,
                 volume_ratio=volume_ratio, extra=self._json(extra)),
        )

    def record_trade(self, *, action: str, symbol: str, qty: float, price: float, notional: float,
                      sentiment, stop_price: float, take_profit: float, reason: str, rationale: str,
                      dry_run: bool, order_id: str, status: str,
                      sector: Optional[str] = None, market_regime: Optional[str] = None) -> None:
        self._execute(
            """
            INSERT INTO trades
                (action, symbol, qty, price, notional, stop_price, take_profit,
                 sentiment_score, sentiment_label, headline_count, positive_headlines,
                 negative_headlines, reason, rationale, dry_run, order_id, status,
                 sector, market_regime)
            VALUES
                (%(action)s, %(symbol)s, %(qty)s, %(price)s, %(notional)s, %(stop_price)s,
                 %(take_profit)s, %(sentiment_score)s, %(sentiment_label)s, %(headline_count)s,
                 %(positive_headlines)s, %(negative_headlines)s, %(reason)s, %(rationale)s,
                 %(dry_run)s, %(order_id)s, %(status)s, %(sector)s, %(market_regime)s)
            """,
            dict(action=action, symbol=symbol, qty=qty, price=price, notional=notional,
                 stop_price=stop_price, take_profit=take_profit,
                 sentiment_score=getattr(sentiment, "score", None),
                 sentiment_label=getattr(sentiment, "label", None),
                 headline_count=getattr(sentiment, "article_count", None),
                 positive_headlines=getattr(sentiment, "positive_count", None),
                 negative_headlines=getattr(sentiment, "negative_count", None),
                 reason=reason, rationale=rationale, dry_run=dry_run, order_id=order_id,
                 status=status, sector=sector, market_regime=market_regime),
        )

    def record_closed_trade(self, *, symbol: str, qty: float, entry_price: float, exit_price: float,
                             pnl: float, pnl_pct: float, exit_reason: str,
                             entry_time: Optional[float] = None, buy_reason: Optional[str] = None,
                             news_summary: Optional[str] = None,
                             sector: Optional[str] = None, confidence_score: Optional[float] = None,
                             confidence_label: Optional[str] = None, market_regime: Optional[str] = None,
                             strategy_version: Optional[str] = None) -> None:
        entry_dt = (
            datetime.fromtimestamp(entry_time, tz=timezone.utc) if entry_time else None
        )
        self._execute(
            """
            INSERT INTO closed_trades
                (symbol, qty, entry_price, exit_price, pnl, pnl_pct, exit_reason,
                 entry_time, buy_reason, news_summary,
                 sector, confidence_score, confidence_label, market_regime, strategy_version)
            VALUES
                (%(symbol)s, %(qty)s, %(entry_price)s, %(exit_price)s, %(pnl)s, %(pnl_pct)s,
                 %(exit_reason)s, %(entry_time)s, %(buy_reason)s, %(news_summary)s,
                 %(sector)s, %(confidence_score)s, %(confidence_label)s, %(market_regime)s,
                 %(strategy_version)s)
            """,
            dict(symbol=symbol, qty=qty, entry_price=entry_price, exit_price=exit_price,
                 pnl=pnl, pnl_pct=pnl_pct, exit_reason=exit_reason, entry_time=entry_dt,
                 buy_reason=buy_reason, news_summary=news_summary,
                 sector=sector, confidence_score=confidence_score, confidence_label=confidence_label,
                 market_regime=market_regime, strategy_version=strategy_version),
        )

    def record_portfolio_snapshot(self, *, portfolio_value: Optional[float], cash: Optional[float],
                                   equity: Optional[float], buying_power: Optional[float],
                                   unrealized_pl: Optional[float], open_positions: Optional[int],
                                   exposure: Optional[float]) -> None:
        self._execute(
            """
            INSERT INTO portfolio_snapshots
                (portfolio_value, cash, equity, buying_power, unrealized_pl, open_positions, exposure)
            VALUES
                (%(portfolio_value)s, %(cash)s, %(equity)s, %(buying_power)s, %(unrealized_pl)s,
                 %(open_positions)s, %(exposure)s)
            """,
            dict(portfolio_value=portfolio_value, cash=cash, equity=equity,
                 buying_power=buying_power, unrealized_pl=unrealized_pl,
                 open_positions=open_positions, exposure=exposure),
        )

    def sync_open_positions(self, rows) -> None:
        """Replace the entire open_positions table with the current book in
        one transaction: delete what's no longer held, upsert what is. `rows`
        is a list of dicts with the columns listed below (entry_time may be
        a unix timestamp or None)."""
        if not self.enabled:
            return
        try:
            conn = self._psycopg2.connect(self.database_url, connect_timeout=5)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dashboard DB connect failed (positions sync dropped): %s", exc)
            self.healthy = False
            self.last_error = str(exc)
            return
        try:
            with conn:
                with conn.cursor() as cur:
                    symbols = [r["symbol"] for r in rows]
                    if symbols:
                        cur.execute(
                            "DELETE FROM open_positions WHERE symbol <> ALL(%s)", (symbols,)
                        )
                    else:
                        cur.execute("DELETE FROM open_positions")
                    for r in rows:
                        entry_time = r.get("entry_time")
                        entry_dt = (
                            datetime.fromtimestamp(entry_time, tz=timezone.utc)
                            if entry_time else None
                        )
                        cur.execute(
                            """
                            INSERT INTO open_positions
                                (symbol, qty, avg_entry_price, current_price, market_value,
                                 unrealized_pl, unrealized_plpc, allocation_pct, ai_confidence,
                                 entry_reason, entry_time, updated_at)
                            VALUES
                                (%(symbol)s, %(qty)s, %(avg_entry_price)s, %(current_price)s,
                                 %(market_value)s, %(unrealized_pl)s, %(unrealized_plpc)s,
                                 %(allocation_pct)s, %(ai_confidence)s, %(entry_reason)s,
                                 %(entry_time)s, now())
                            ON CONFLICT (symbol) DO UPDATE SET
                                qty = EXCLUDED.qty, avg_entry_price = EXCLUDED.avg_entry_price,
                                current_price = EXCLUDED.current_price,
                                market_value = EXCLUDED.market_value,
                                unrealized_pl = EXCLUDED.unrealized_pl,
                                unrealized_plpc = EXCLUDED.unrealized_plpc,
                                allocation_pct = EXCLUDED.allocation_pct,
                                ai_confidence = EXCLUDED.ai_confidence,
                                entry_reason = EXCLUDED.entry_reason,
                                entry_time = EXCLUDED.entry_time,
                                updated_at = now()
                            """,
                            dict(
                                symbol=r["symbol"], qty=r.get("qty"),
                                avg_entry_price=r.get("avg_entry_price"),
                                current_price=r.get("current_price"),
                                market_value=r.get("market_value"),
                                unrealized_pl=r.get("unrealized_pl"),
                                unrealized_plpc=r.get("unrealized_plpc"),
                                allocation_pct=r.get("allocation_pct"),
                                ai_confidence=r.get("ai_confidence"),
                                entry_reason=r.get("entry_reason"),
                                entry_time=entry_dt,
                            ),
                        )
            self.healthy = True
            self.last_error = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dashboard DB positions sync failed: %s", exc)
            self.healthy = False
            self.last_error = str(exc)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def record_notification(self, *, type_: str, title: str, message: Optional[str] = None,
                             severity: str = "info", metadata: Optional[Dict] = None) -> None:
        self._execute(
            """
            INSERT INTO notifications (type, severity, title, message, metadata)
            VALUES (%(type)s, %(severity)s, %(title)s, %(message)s, %(metadata)s)
            """,
            dict(type=type_, severity=severity, title=title, message=message,
                 metadata=self._json(metadata)),
        )
