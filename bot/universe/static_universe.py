"""A bundled list of liquid, frequently-covered US tickers.

Used either as the whole universe (provider = static) or to top up the
mention-based list when the news feed yields too few names. This is a curated
convenience list, not an exhaustive market map — edit it freely.
"""
from __future__ import annotations

from typing import List

STATIC_UNIVERSE: List[str] = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "ORCL", "AMD",
    "ADBE", "CRM", "INTC", "CSCO", "QCOM", "TXN", "MU", "PLTR", "NFLX", "IBM",
    # Financials
    "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "V", "MA", "PYPL",
    # Healthcare
    "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT",
    # Consumer / industrial / energy
    "WMT", "COST", "HD", "MCD", "NKE", "SBUX", "DIS", "KO", "PEP", "PG",
    "XOM", "CVX", "BA", "CAT", "GE", "F", "GM", "UBER", "COIN", "T",
]


def get_static_universe(limit: int) -> List[str]:
    return STATIC_UNIVERSE[:limit]


# Rough sector tags, used only by the optional "max positions per sector" cap so
# the bot doesn't pour half the book into one correlated group (e.g. all chips).
# Names not listed here resolve to "unknown" and are exempt from the cap.
SECTORS = {
    # Semiconductors (highly correlated as a group)
    "NVDA": "semis", "AMD": "semis", "AVGO": "semis", "INTC": "semis",
    "QCOM": "semis", "TXN": "semis", "MU": "semis",
    # Software / internet / mega-cap tech
    "AAPL": "tech", "MSFT": "tech", "GOOGL": "tech", "AMZN": "tech",
    "META": "tech", "ORCL": "tech", "ADBE": "tech", "CRM": "tech",
    "CSCO": "tech", "PLTR": "tech", "NFLX": "tech", "IBM": "tech",
    # Financials
    "JPM": "financials", "BAC": "financials", "WFC": "financials", "GS": "financials",
    "MS": "financials", "C": "financials", "BLK": "financials", "SCHW": "financials",
    "AXP": "financials", "V": "financials", "MA": "financials", "PYPL": "financials",
    "COIN": "financials",
    # Healthcare
    "UNH": "healthcare", "JNJ": "healthcare", "LLY": "healthcare", "PFE": "healthcare",
    "MRK": "healthcare", "ABBV": "healthcare", "TMO": "healthcare", "ABT": "healthcare",
    # Consumer
    "WMT": "consumer", "COST": "consumer", "HD": "consumer", "MCD": "consumer",
    "NKE": "consumer", "SBUX": "consumer", "DIS": "consumer", "KO": "consumer",
    "PEP": "consumer", "PG": "consumer", "TSLA": "consumer", "UBER": "consumer",
    # Energy / industrials / autos / telecom
    "XOM": "energy", "CVX": "energy", "BA": "industrials", "CAT": "industrials",
    "GE": "industrials", "F": "autos", "GM": "autos", "T": "telecom",
}


def sector_of(symbol: str) -> str:
    return SECTORS.get(symbol, "unknown")
