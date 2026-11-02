"""Operational-safety components (Stage 2): DB health gate, local journal,
recovery reconciliation and cycle watchdog. None of these change trading
rules; they decide whether NEW entries are allowed and make failures loud."""
