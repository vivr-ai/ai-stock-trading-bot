"""Reconciliation pass run by the DB health gate at start-up and after a DB
outage, before new entries are allowed again.

Succeeds only if:
  1. every pending journal entry was copied into Postgres (journal_events), and
  2. the broker's positions AND open protective orders could both be read.

It also reports (but does not by itself fix) held positions without a
protective broker order and positions with no buy row in the DB. Both are
handled by the existing per-cycle adoption pass (_adopt_legacy_positions),
which re-protects and backfills; the counts appear in the recovery alert.
Nothing here places, cancels or modifies a broker order.
"""
from __future__ import annotations

import logging
from typing import Tuple

logger = logging.getLogger(__name__)


def make_reconciler(recorder, broker, journal):
    def reconcile() -> Tuple[bool, str]:
        parts = []
        if journal is not None:
            pending = journal.pending()
            if pending:
                copied = recorder.replay_journal(pending)
                if copied is None or copied < len(pending):
                    return False, f"journal replay incomplete ({copied or 0}/{len(pending)})"
                journal.mark_replayed([e["id"] for e in pending])
                parts.append(f"{copied} journal entries copied")
        try:
            positions = broker.open_positions_detailed()
        except Exception as exc:  # noqa: BLE001
            return False, f"could not read broker positions: {exc}"
        protected = broker.get_protected_symbols()
        if protected is None:
            return False, "could not read broker protective orders"
        unprotected = sorted(s for s in positions if s not in protected)
        no_row = sorted(s for s in positions if recorder.get_last_buy_trade(s) is None)
        parts.append(f"{len(positions)} positions held")
        if unprotected:
            parts.append(f"UNPROTECTED (adoption will re-protect): {', '.join(unprotected)}")
        if no_row:
            parts.append(f"no DB buy row (adoption will backfill): {', '.join(no_row)}")
        return True, "; ".join(parts)

    return reconcile
