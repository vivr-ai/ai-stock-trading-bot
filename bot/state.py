"""Tiny JSON-file memory so the bot remembers things between runs.

Three things are tracked:
  * last_exit[symbol]   -> unix time we last exited a name (for re-entry cooldown)
  * last_held           -> symbols held at the end of the previous cycle, so
                           that if a name has vanished since (it hit its stop
                           or target, or was sold), we can put it on cooldown
                           automatically AND record a closed trade for the
                           performance report.
  * open_lots[symbol]   -> {entry_price, qty, entry_time} recorded the moment
                           we buy, so that whenever the position closes (either
                           because we sold it on sentiment, or because a
                           bracket stop/take-profit fired at the broker) we can
                           compute realized P/L without re-deriving it from the
                           broker's account-activity feed.

Plain English: "if we just got out of a stock, wait a while before buying it
again, so we don't flip-flop in and out and rack up churn" plus "remember what
we paid so we can report what we made or lost when it closes."

IMPORTANT (Railway): this file lives on local disk. Railway's filesystem is
ephemeral unless you attach a Volume — without one, this state (and therefore
cooldowns + open-lot tracking for the performance report) resets on every
redeploy. See DEPLOYMENT.md.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class BotState:
    def __init__(self, path: str):
        self.path = path
        self._data = {"last_exit": {}, "last_held": [], "open_lots": {}}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    self._data = json.load(f)
            except (ValueError, OSError) as exc:
                logger.warning("Could not read state file %s (%s); starting fresh.",
                               self.path, exc)
        self._data.setdefault("last_exit", {})
        self._data.setdefault("last_held", [])
        self._data.setdefault("open_lots", {})

    def _save(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(self._data, f)
            os.replace(tmp, self.path)  # atomic write
        except OSError as exc:
            logger.error("Could not persist state to %s: %s", self.path, exc)

    def in_cooldown(self, symbol: str, hours: float) -> bool:
        ts = self._data["last_exit"].get(symbol)
        if not ts:
            return False
        return (time.time() - ts) < hours * 3600.0

    def mark_exit(self, symbol: str) -> None:
        self._data["last_exit"][symbol] = time.time()
        self._save()

    def detect_exits(self, current_held: List[str]) -> List[str]:
        """Compare to last cycle; any name that disappeared just exited.

        This catches bracket take-profit / stop-loss fills that happened at
        the broker between cycles, not just sells the bot made itself.
        """
        prev = set(self._data.get("last_held", []))
        now = set(current_held)
        exited = sorted(prev - now)
        for sym in exited:
            self._data["last_exit"][sym] = time.time()
        self._data["last_held"] = sorted(now)
        self._save()
        return exited

    # ---- open-lot tracking (for realized P/L on exit) ----------------------
    def record_open(self, symbol: str, entry_price: float, qty: int,
                     reason: str = None, sentiment_score: float = None,
                     sentiment_label: str = None) -> None:
        self._data["open_lots"][symbol] = {
            "entry_price": entry_price,
            "qty": qty,
            "entry_time": time.time(),
            "reason": reason,
            "sentiment_score": sentiment_score,
            "sentiment_label": sentiment_label,
        }
        self._save()

    def pop_open(self, symbol: str) -> Optional[Dict]:
        """Remove and return the open-lot record for symbol, if any."""
        lot = self._data["open_lots"].pop(symbol, None)
        self._save()
        return lot

    def peek_open(self, symbol: str) -> Optional[Dict]:
        return self._data["open_lots"].get(symbol)
