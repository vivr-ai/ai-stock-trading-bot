"""Config for the investment tracker, read entirely from environment
variables (+ one JSON file). Deliberately not sharing bot/config.py - this
module has to stay usable even if nothing else in the repo changes.

Env vars (all optional except the Telegram pair, without which the scan
still runs and logs its findings, it just can't send the alert):
  INVEST_TELEGRAM_BOT_TOKEN   - bot token from @BotFather, for THIS bot
                                 (deliberately separate from the trading
                                 bot's TELEGRAM_BOT_TOKEN - a different
                                 Telegram bot was created for this feature)
  INVEST_TELEGRAM_CHAT_ID     - chat id to send alerts to
  INVEST_HOLDINGS_FILE        - path to holdings.json (default: alongside
                                 this file)
  INVEST_MARKET_TIMEZONE      - IANA tz for the daily scan's schedule
                                 (default: Australia/Sydney)
  INVEST_SCAN_HOUR / INVEST_SCAN_MINUTE
                               - local time (in the timezone above) to run
                                 the daily scan (default: 10, 15 - a little
                                 after the ASX/NZX open so the day's first
                                 trade price is live)
  LOG_LEVEL                   - default INFO
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("investing.config")

_DEFAULT_HOLDINGS_FILE = Path(__file__).resolve().parent / "holdings.json"


@dataclass
class Holding:
    symbol: str
    exchange: str
    benchmark: str
    shares: float
    ac_share: float
    note: Optional[str] = None


@dataclass
class Config:
    telegram_bot_token: str
    telegram_chat_id: str
    holdings_file: Path
    market_timezone: str
    scan_hour: int
    scan_minute: int
    log_level: str
    holdings: List[Holding] = field(default_factory=list)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


def load_holdings(path: Path) -> List[Holding]:
    if not path.exists():
        logger.warning("Holdings file not found at %s - scan will have nothing to check.", path)
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Could not read/parse holdings file %s: %s", path, exc)
        return []

    holdings: List[Holding] = []
    for i, row in enumerate(data.get("holdings", [])):
        try:
            holdings.append(Holding(
                symbol=row["symbol"],
                exchange=row["exchange"],
                benchmark=row["benchmark"],
                shares=float(row["shares"]),
                ac_share=float(row["ac_share"]),
                note=row.get("note"),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            logger.error("Skipping malformed holdings.json entry #%d (%r): %s", i, row, exc)
    return holdings


def load_config() -> Config:
    holdings_file = Path(os.environ.get("INVEST_HOLDINGS_FILE", "") or _DEFAULT_HOLDINGS_FILE)
    cfg = Config(
        telegram_bot_token=os.environ.get("INVEST_TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("INVEST_TELEGRAM_CHAT_ID", ""),
        holdings_file=holdings_file,
        market_timezone=os.environ.get("INVEST_MARKET_TIMEZONE", "Australia/Sydney"),
        scan_hour=int(os.environ.get("INVEST_SCAN_HOUR", "10")),
        scan_minute=int(os.environ.get("INVEST_SCAN_MINUTE", "15")),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
    cfg.holdings = load_holdings(holdings_file)
    if not cfg.telegram_enabled:
        logger.warning(
            "INVEST_TELEGRAM_BOT_TOKEN / INVEST_TELEGRAM_CHAT_ID not both set - "
            "scan will run and log results but cannot send alerts."
        )
    for h in cfg.holdings:
        if h.note:
            logger.warning("Holding %s has an unresolved note: %s", h.symbol, h.note)
    return cfg
