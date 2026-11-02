"""Start-up guard: make sure this process is talking to the Alpaca account it
is supposed to trade, before any order can be placed.

Why: staging and production run the same code. If a staging deployment were
ever given production's API keys (copy-paste, a duplicated Railway
environment), it would trade the production account. The only reliable way to
know which account a key belongs to is to ask Alpaca - so we do, once, at
start-up, and refuse to run on a mismatch.

Behaviour:
  * EXPECTED_ALPACA_ACCOUNT_NUMBER unset (production today): no check, start
    exactly as before (one informational log line with the account number).
  * set, and Alpaca reports the same account number: start normally.
  * set, and it differs: raise AccountMismatchError (the process exits).
  * set, but Alpaca can't be reached to check: raise as well (fail closed) -
    an unverifiable account is treated as the wrong account.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class AccountMismatchError(RuntimeError):
    """The API keys belong to a different Alpaca account than expected."""


def _mask(number: str) -> str:
    number = str(number or "")
    return ("*" * max(0, len(number) - 4)) + number[-4:] if number else "(none)"


def verify_account_identity(cfg, broker) -> str:
    """Returns the connected account number (masked) or raises
    AccountMismatchError. See the module docstring for the rules."""
    expected = (cfg.deployment.expected_account_number or "").strip()
    env = cfg.deployment.environment
    try:
        ident = broker.account_identity()
    except Exception as exc:  # noqa: BLE001
        if expected:
            raise AccountMismatchError(
                f"[{env}] Could not verify which Alpaca account these keys belong to "
                f"({exc}); EXPECTED_ALPACA_ACCOUNT_NUMBER is set, so refusing to start."
            ) from exc
        logger.warning("Could not read Alpaca account identity at start-up: %s", exc)
        return "(unknown)"

    actual = str(ident.get("account_number") or "").strip()
    masked = _mask(actual)
    if expected and actual != expected:
        raise AccountMismatchError(
            f"[{env}] Alpaca keys belong to account {masked}, but "
            f"EXPECTED_ALPACA_ACCOUNT_NUMBER is {_mask(expected)}. Refusing to start - "
            "check which keys are configured for this environment."
        )
    logger.info("Alpaca account verified for %s: %s (%s)%s", env, masked,
                "paper" if ident.get("paper") else "LIVE",
                "" if expected else " - no EXPECTED_ALPACA_ACCOUNT_NUMBER set, identity not enforced")
    return masked
