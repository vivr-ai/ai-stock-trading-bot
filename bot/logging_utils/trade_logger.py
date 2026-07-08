"""Append-only CSV trade logger.

One row per buy/sell decision (including dry-run decisions): a complete audit
trail of what the bot did, the sentiment behind it, and the reason it fired.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timezone

_FIELDS = [
    "timestamp_utc",
    "action",
    "symbol",
    "qty",
    "price",
    "notional",
    "sentiment_score",
    "sentiment_label",
    "positive_headlines",
    "negative_headlines",
    "headline_count",
    "stop_price",
    "take_profit",
    "reason",
    "rationale",
    "dry_run",
    "order_id",
    "status",
]


class TradeLogger:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=_FIELDS).writeheader()

    def log(self, action, symbol, qty, price, notional, sentiment,
            stop_price, take_profit, reason, dry_run, order_id, status) -> None:
        row = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "symbol": symbol,
            "qty": qty,
            "price": round(price, 4),
            "notional": round(notional, 2),
            "sentiment_score": round(sentiment.score, 2),
            "sentiment_label": sentiment.label,
            "positive_headlines": sentiment.positive_count,
            "negative_headlines": sentiment.negative_count,
            "headline_count": sentiment.article_count,
            "stop_price": stop_price,
            "take_profit": take_profit,
            "reason": reason,
            "rationale": sentiment.rationale,
            "dry_run": dry_run,
            "order_id": order_id,
            "status": status,
        }
        with open(self.path, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=_FIELDS).writerow(row)
