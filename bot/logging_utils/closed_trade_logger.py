"""Append-only CSV ledger of CLOSED round-trip trades (one row per exit),
with realized P/L — the source of truth for the win-rate / avg-gain /
avg-loss numbers in the daily performance report.

A row is written whenever a position that BotState was tracking as an open
lot closes, whether the bot closed it itself (sentiment sell) or the broker
closed it via a bracket stop-loss/take-profit fill (detected by
BotState.detect_exits on the next cycle).
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from typing import Optional

_FIELDS = [
    "timestamp_utc",
    "symbol",
    "qty",
    "entry_price",
    "exit_price",
    "pnl",
    "pnl_pct",
    "exit_reason",
    "entry_time_utc",
]


class ClosedTradeLogger:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=_FIELDS).writeheader()

    def log(self, symbol: str, qty: float, entry_price: float, exit_price: float,
             exit_reason: str, entry_time: Optional[float] = None) -> float:
        """Write one closed-trade row; returns the realized P/L for it."""
        pnl = (exit_price - entry_price) * qty
        pnl_pct = ((exit_price - entry_price) / entry_price * 100.0) if entry_price else 0.0
        row = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "qty": qty,
            "entry_price": round(entry_price, 4),
            "exit_price": round(exit_price, 4),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "exit_reason": exit_reason,
            "entry_time_utc": (
                datetime.fromtimestamp(entry_time, tz=timezone.utc).isoformat()
                if entry_time else ""
            ),
        }
        with open(self.path, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=_FIELDS).writerow(row)
        return pnl
