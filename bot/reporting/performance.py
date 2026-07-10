"""Daily performance report: portfolio value, realized + unrealized P/L, open
positions, trade count, win rate, average gain/loss, and max drawdown.

Data sources:
  * portfolio value, buying power           -> broker.account_snapshot()
  * open positions + unrealized P/L          -> broker.open_positions_detailed()
                                                 (straight from Alpaca's Position
                                                 objects — we don't recompute it)
  * realized P/L, win rate, avg gain/loss    -> logs/closed_trades.csv (written by
                                                 ClosedTradeLogger whenever a
                                                 position closes, sentiment-sell
                                                 or bracket auto-exit alike)
  * trade count today                        -> logs/trades.csv
  * max drawdown                             -> broker.portfolio_history() (Alpaca's
                                                 equity time series); omitted (not
                                                 estimated) if the endpoint is
                                                 unavailable, rather than guessing.

Written to <report_dir>/report_<date>.json (machine-readable) and
<report_dir>/report_<date>.txt (human-readable), and also logged as a single
structured log line so it shows up in Railway's log stream even if you never
open the file.
"""
from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger("bot.reporting.performance")


def _read_csv(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _max_drawdown_pct(equity: List[float]) -> Optional[float]:
    if not equity:
        return None
    peak = equity[0]
    worst = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak * 100.0
            worst = max(worst, dd)
    return worst


class PerformanceReporter:
    def __init__(self, broker, trade_log_path: str, closed_trades_path: str, report_dir: str):
        self.broker = broker
        self.trade_log_path = trade_log_path
        self.closed_trades_path = closed_trades_path
        self.report_dir = report_dir

    def build(self) -> Dict:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        try:
            acct = self.broker.account_snapshot()
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not fetch account snapshot for report: %s", exc)
            acct = {"portfolio_value": None, "equity": None, "buying_power": None, "cash": None}

        positions = self.broker.open_positions_detailed()
        unrealized_pl = sum(p.unrealized_pl for p in positions.values())

        closed_rows = _read_csv(self.closed_trades_path)
        closed_pnls = []
        for r in closed_rows:
            try:
                closed_pnls.append(float(r["pnl"]))
            except (KeyError, ValueError):
                continue
        wins = [p for p in closed_pnls if p > 0]
        losses = [p for p in closed_pnls if p < 0]
        win_rate = (len(wins) / len(closed_pnls) * 100.0) if closed_pnls else None
        avg_gain = (sum(wins) / len(wins)) if wins else None
        avg_loss = (sum(losses) / len(losses)) if losses else None
        realized_pl_all_time = sum(closed_pnls) if closed_pnls else 0.0
        realized_pl_today = sum(
            float(r["pnl"]) for r in closed_rows
            if r.get("timestamp_utc", "").startswith(today) and _is_float(r.get("pnl"))
        )

        trade_rows = _read_csv(self.trade_log_path)
        trades_today = [r for r in trade_rows if r.get("timestamp_utc", "").startswith(today)]
        buys_today = sum(1 for r in trades_today if r.get("action") == "buy")
        sells_today = sum(1 for r in trades_today if r.get("action") == "sell")

        equity_curve = self.broker.portfolio_history()
        max_drawdown = _max_drawdown_pct(equity_curve) if equity_curve else None

        return {
            "date": today,
            "portfolio_value": acct.get("portfolio_value"),
            "cash": acct.get("cash"),
            "buying_power": acct.get("buying_power"),
            "realized_pl_today": round(realized_pl_today, 2),
            "realized_pl_all_time": round(realized_pl_all_time, 2),
            "unrealized_pl": round(unrealized_pl, 2),
            "open_positions_count": len(positions),
            "open_positions": [
                {
                    "symbol": p.symbol, "qty": p.qty, "avg_entry_price": p.avg_entry_price,
                    "current_price": p.current_price, "market_value": p.market_value,
                    "unrealized_pl": p.unrealized_pl, "unrealized_plpc": p.unrealized_plpc,
                }
                for p in positions.values()
            ],
            "trades_today": {"buys": buys_today, "sells": sells_today, "total": len(trades_today)},
            "closed_trades_all_time": len(closed_pnls),
            "win_rate_pct": round(win_rate, 1) if win_rate is not None else None,
            "avg_gain": round(avg_gain, 2) if avg_gain is not None else None,
            "avg_loss": round(avg_loss, 2) if avg_loss is not None else None,
            "max_drawdown_pct": round(max_drawdown, 2) if max_drawdown is not None else None,
        }

    def render_text(self, report: Dict) -> str:
        pv = report["portfolio_value"]
        lines = [
            f"=== DAILY PERFORMANCE REPORT — {report['date']} ===",
            f"Portfolio value:      {'$%.2f' % pv if pv is not None else 'unavailable'}",
            f"Realized P/L (today):  ${report['realized_pl_today']:.2f}",
            f"Realized P/L (total):  ${report['realized_pl_all_time']:.2f}",
            f"Unrealized P/L (open): ${report['unrealized_pl']:.2f}",
            f"Open positions:        {report['open_positions_count']}",
        ]
        for p in report["open_positions"]:
            lines.append(
                f"  - {p['symbol']}: qty={p['qty']} entry={p['avg_entry_price']:.2f} "
                f"now={p['current_price']:.2f} unrealized_pl={p['unrealized_pl']:.2f} "
                f"({p['unrealized_plpc']:.1f}%)"
            )
        lines += [
            f"Trades today:          {report['trades_today']['total']} "
            f"(buys={report['trades_today']['buys']}, sells={report['trades_today']['sells']})",
            f"Closed trades (all-time): {report['closed_trades_all_time']}",
            f"Win rate:              "
            f"{report['win_rate_pct']}%" if report["win_rate_pct"] is not None else
            "Win rate:              n/a (no closed trades yet)",
            f"Average gain:          "
            f"${report['avg_gain']:.2f}" if report["avg_gain"] is not None else
            "Average gain:          n/a",
            f"Average loss:          "
            f"${report['avg_loss']:.2f}" if report["avg_loss"] is not None else
            "Average loss:          n/a",
            f"Max drawdown:          "
            f"{report['max_drawdown_pct']:.2f}%" if report["max_drawdown_pct"] is not None else
            "Max drawdown:          unavailable this run",
        ]
        return "\n".join(lines)

    def build_weekly(self) -> Dict:
        """A lightweight 7-day rollup, reusing the same closed_trades.csv this
        report already reads - no new data source, just a wider date filter.
        Meant to be called once a week (see main.py's Friday EOD check), not
        every day."""
        now = datetime.now(timezone.utc)
        week_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")

        try:
            acct = self.broker.account_snapshot()
            portfolio_value = acct.get("portfolio_value")
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not fetch account snapshot for weekly report: %s", exc)
            portfolio_value = None

        closed_rows = _read_csv(self.closed_trades_path)
        week_pnls = [
            float(r["pnl"]) for r in closed_rows
            if r.get("timestamp_utc", "") >= week_start and _is_float(r.get("pnl"))
        ]
        wins = [p for p in week_pnls if p > 0]
        losses = [p for p in week_pnls if p < 0]
        win_rate = (len(wins) / len(week_pnls) * 100.0) if week_pnls else None

        trade_rows = _read_csv(self.trade_log_path)
        trades_week = [r for r in trade_rows if r.get("timestamp_utc", "") >= week_start]

        return {
            "week_ending": now.strftime("%Y-%m-%d"),
            "portfolio_value": portfolio_value,
            "realized_pl_week": round(sum(week_pnls), 2) if week_pnls else 0.0,
            "trades_week": len(trades_week),
            "closed_trades_week": len(week_pnls),
            "win_rate_pct": round(win_rate, 1) if win_rate is not None else None,
            "wins": len(wins),
            "losses": len(losses),
        }

    def render_weekly_text(self, report: Dict) -> str:
        pv = report["portfolio_value"]
        win_rate_line = (
            f"Win rate (7 days):      {report['win_rate_pct']}%"
            if report["win_rate_pct"] is not None
            else "Win rate (7 days):      n/a (no closed trades this week)"
        )
        return "\n".join([
            f"=== WEEKLY SUMMARY — week ending {report['week_ending']} ===",
            f"Portfolio value:        {'$%.2f' % pv if pv is not None else 'unavailable'}",
            f"Realized P/L (7 days):  ${report['realized_pl_week']:.2f}",
            f"Trades placed (7 days): {report['trades_week']}",
            f"Closed trades (7 days): {report['closed_trades_week']} "
            f"(wins={report['wins']}, losses={report['losses']})",
            win_rate_line,
        ])

    def write_weekly(self) -> str:
        report = self.build_weekly()
        text = self.render_weekly_text(report)
        logger.info("Weekly summary generated", extra={"decision": "weekly_report", **report})
        return text

    def write(self) -> str:
        report = self.build()
        text = self.render_text(report)

        os.makedirs(self.report_dir, exist_ok=True)
        json_path = os.path.join(self.report_dir, f"report_{report['date']}.json")
        txt_path = os.path.join(self.report_dir, f"report_{report['date']}.txt")
        try:
            with open(json_path, "w") as f:
                json.dump(report, f, indent=2, default=str)
            with open(txt_path, "w") as f:
                f.write(text + "\n")
        except OSError as exc:
            logger.error("Could not write report files to %s: %s", self.report_dir, exc)

        logger.info("Daily performance report generated", extra={"decision": "daily_report", **report})
        return text


def _is_float(raw) -> bool:
    try:
        float(raw)
        return True
    except (TypeError, ValueError):
        return False
