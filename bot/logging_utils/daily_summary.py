"""Daily summary log.

Writes a human-readable line to a log file after each cycle (portfolio value,
open positions, exposure, and how many buys/sells/blocks happened), and an
end-of-day rollup aggregated from the trades CSV.
"""
from __future__ import annotations

import csv
import os
from collections import Counter
from datetime import datetime, timezone


class DailySummaryLogger:
    def __init__(self, summary_path: str, trade_log_path: str):
        self.summary_path = summary_path
        self.trade_log_path = trade_log_path
        os.makedirs(os.path.dirname(os.path.abspath(summary_path)), exist_ok=True)

    def _write(self, line: str) -> None:
        with open(self.summary_path, "a") as f:
            f.write(line + "\n")

    def log_cycle(self, portfolio_value, open_positions, exposure, stats) -> None:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._write(
            f"[{ts}] CYCLE pv=${portfolio_value:,.2f} positions={open_positions} "
            f"exposure=${exposure:,.2f} evaluated={stats.evaluated} "
            f"buys={stats.buys} sells={stats.sells} blocked={stats.blocked}"
        )

    def write_eod(self, portfolio_value: float) -> None:
        """Aggregate today's trades from the CSV into an end-of-day summary."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        buys = sells = 0
        by_symbol: Counter = Counter()

        if os.path.exists(self.trade_log_path):
            with open(self.trade_log_path, newline="") as f:
                for row in csv.DictReader(f):
                    if not row.get("timestamp_utc", "").startswith(today):
                        continue
                    if row["action"] == "buy":
                        buys += 1
                    elif row["action"] == "sell":
                        sells += 1
                    by_symbol[row["symbol"]] += 1

        top = ", ".join(f"{s}({n})" for s, n in by_symbol.most_common(10)) or "none"
        self._write(
            f"[{today}] === DAILY SUMMARY === portfolio_value=${portfolio_value:,.2f} "
            f"total_buys={buys} total_sells={sells} symbols_traded={len(by_symbol)} "
            f"| activity: {top}"
        )
