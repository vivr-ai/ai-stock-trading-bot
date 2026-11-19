"""DQ3 (review 19 Nov): the ledger records what the broker actually executed.

A buy's `trades` row, the open-lot record and every P/L derived from them
use Alpaca's own `filled_avg_price` and `filled_qty` for the order, never the
decision-time quote. Alpaca's `filled_avg_price` is already the
quantity-weighted average across every partial fill of the order, so the
aggregate is the broker's, not ours.

Flow
  1. Right after a buy is accepted, `resolve_entry_fill` looks the order up
     (GET only, a few times over at most `timeout_s`). Paper market orders
     normally finish in well under a second.
  2. Final status (filled / canceled / expired / rejected):
     the row is written with the broker's numbers, `fill_confirmed=TRUE` and
     the decision-time price kept separately in `requested_price`.
  3. Not final yet (still new / partially filled) or the lookup failed: the
     row is written as PROVISIONAL - `fill_confirmed=FALSE`, price and qty
     are the requested ones and clearly marked so - and every later cycle
     re-checks it. When the broker reports a final status, the row is
     completed once (`Recorder.confirm_entry_fill`, which only ever touches
     rows still FALSE). A confirmed row is never rewritten, so a restart or a
     reconciliation can't put a requested price back over a real fill.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Statuses after which Alpaca will not add more fills to the order.
# "done_for_day" is deliberately NOT final: the entry is a GTC order, and
# Alpaca says such an order can resume and fill further on the next trading
# day, so it stays provisional until it really ends.
FINAL_STATUSES = frozenset({"filled", "canceled", "cancelled", "expired", "rejected"})


def _status(order: dict) -> str:
    s = str(order.get("status") or "").lower()
    return s.split(".")[-1]          # tolerate "OrderStatus.FILLED" style values


def _num(x) -> Optional[float]:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


@dataclass
class EntryFill:
    requested_price: float
    requested_qty: float
    price: float             # broker filled_avg_price when confirmed, else requested_price
    qty: float               # broker filled_qty when confirmed, else requested_qty
    status: str              # broker status as last seen ("unknown" if never read)
    confirmed: bool          # broker reported a final status; price/qty are the broker's

    @property
    def filled(self) -> bool:
        """Shares actually held from this order (False for a provisional row:
        unknown yet, so the caller keeps the requested figures as a placeholder)."""
        return (not self.confirmed) or self.qty > 0

    @property
    def notional(self) -> float:
        return self.price * self.qty


def fill_from_order(order: Optional[dict], requested_price: float, requested_qty: float) -> EntryFill:
    """Interpret one broker order read. Final status -> broker numbers; else provisional."""
    if not order:
        return EntryFill(requested_price, requested_qty, requested_price, requested_qty, "unknown", False)
    status = _status(order)
    fq = _num(order.get("filled_qty")) or 0.0
    fap = _num(order.get("filled_avg_price"))
    if status in FINAL_STATUSES:
        if fq > 0 and fap:
            return EntryFill(requested_price, requested_qty, fap, fq, status, True)
        if fq <= 0:
            # Final with nothing filled (rejected / canceled before any fill).
            return EntryFill(requested_price, requested_qty, requested_price, 0.0, status, True)
    # Not final (or a final status with an inconsistent fill): provisional.
    return EntryFill(requested_price, requested_qty, requested_price, requested_qty, status or "unknown", False)


def resolve_entry_fill(broker, order_id: str, requested_price: float, requested_qty: float, *,
                       timeout_s: float = 10.0, poll_s: float = 0.5,
                       sleep: Callable[[float], None] = time.sleep,
                       clock: Callable[[], float] = time.monotonic) -> EntryFill:
    """Read the order until the broker reports a final status or `timeout_s`
    passes. GET requests only - it never sends, changes or cancels anything.
    A broker without `order_snapshot` (or a failed read) gives a provisional
    result immediately once the time is up."""
    reader = getattr(broker, "order_snapshot", None)
    last = None
    if reader is None or not order_id:
        return fill_from_order(None, requested_price, requested_qty)
    deadline = clock() + max(0.0, timeout_s)
    while True:
        try:
            snap = reader(order_id)
        except Exception as exc:  # noqa: BLE001 - a read problem only delays confirmation
            logger.warning("Fill lookup for order %s failed: %s", order_id, exc)
            snap = None
        if snap is not None and getattr(snap, "ok", False) and snap.data:
            last = snap.data
            f = fill_from_order(last, requested_price, requested_qty)
            if f.confirmed:
                return f
        if clock() >= deadline:
            return fill_from_order(last, requested_price, requested_qty) if last else \
                fill_from_order(None, requested_price, requested_qty)
        sleep(poll_s)
