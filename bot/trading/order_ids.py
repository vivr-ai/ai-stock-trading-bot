"""Deterministic client order IDs (Reliability plan v3).

    <env><kind>-<SYMBOL>-<position ref>-<attempt>
      env   's' staging / 'p' production
      kind  'P' protective order, 'R' take-profit replacement, 'X' any exit sell
      ref   first 12 hex characters of the Alpaca order that opened the position
            (or a value derived from the position when no buy order is known)
      n     1 + the highest attempt of this kind already at Alpaca for this
            position; a new number only once the previous order is finished

Two instances (or one instance before and after a restart) deciding the same
action compute the same ID. The ID alone is never trusted for idempotency:
callers also look the ID up and check the broker's positions and orders.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

WORKING = {"new", "accepted", "pending_new", "held", "partially_filled", "pending_replace",
           "accepted_for_bidding", "calculated", "pending_cancel"}


def position_ref(entry_order_id: Optional[str], position: dict) -> str:
    if entry_order_id:
        return re.sub(r"[^0-9a-f]", "", str(entry_order_id).lower())[:12] or "unknown"
    return f"u{int(round((position.get('avg_entry_price') or 0) * 100))}q{int(position.get('qty') or 0)}"


def order_id(env_tag: str, kind: str, symbol: str, ref: str, attempt: int) -> str:
    return f"{env_tag}{kind}-{symbol}-{ref}-{attempt}"


def prefix_for(env_tag: str, kind: str, symbol: str, ref: str) -> str:
    return f"{env_tag}{kind}-{symbol}-{ref}-"


def attempts_at_broker(orders: List[dict], prefix: str) -> Tuple[int, bool]:
    """(highest attempt number already used, any of them still working)."""
    best, working = 0, False
    for o in orders:
        cid = o.get("client_order_id") or ""
        if not cid.startswith(prefix):
            continue
        tail = cid[len(prefix):]
        if tail.isdigit():
            best = max(best, int(tail))
        if (o.get("status") or "") in WORKING:
            working = True
    return best, working
