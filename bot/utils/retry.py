"""Shared retry helper for every external API call the bot makes.

Every network call the bot depends on (Alpaca trading/data, Finnhub,
NewsAPI, Claude, OpenAI) is wrapped with the same policy: exponential
backoff with jitter, a bounded number of attempts, and structured logging
of each failed attempt. When attempts are exhausted the *caller* decides
what "safe" means (empty article list, neutral sentiment, skip this
symbol this cycle) — this helper only ever raises the final exception; it
never crashes the process, because every call site catches it and
degrades.

Tunable via config/env: RETRY_MAX_ATTEMPTS, RETRY_BASE_DELAY_SECONDS.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Callable, Iterable, Optional, Tuple, TypeVar

logger = logging.getLogger("bot.retry")

R = TypeVar("R")

# Defaults used when a call site doesn't have a Config object handy (e.g.
# module-level constants). RetryConfig from bot.config overrides these.
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BASE_DELAY = 1.0
MAX_DELAY = 30.0


def call_with_retry(
    fn: Callable[[], R],
    *,
    attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = MAX_DELAY,
    retry_on: Tuple[type, ...] = (Exception,),
    op_name: str = "operation",
) -> R:
    """Call fn() with exponential backoff + jitter.

    Raises the last exception if every attempt fails, so the caller can
    apply its own domain-specific fallback (empty list, neutral score,
    None, etc.) rather than this helper guessing one.
    """
    last_exc: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except retry_on as exc:  # noqa: BLE001 - intentionally broad, caller narrows via retry_on
            last_exc = exc
            if attempt >= attempts:
                logger.error(
                    "%s failed permanently after %d attempt(s): %s",
                    op_name, attempt, exc,
                )
                break
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.25)  # jitter, avoid thundering herd
            logger.warning(
                "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                op_name, attempt, attempts, exc, delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def retrying(
    *,
    attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    op_name: str = "operation",
):
    """Decorator form of call_with_retry, for methods with no arguments to
    partially-apply (use call_with_retry directly when you need to pass a
    lambda capturing arguments — the common case in this codebase)."""

    def decorator(fn: Callable[..., R]) -> Callable[..., R]:
        def wrapper(*args, **kwargs) -> R:
            return call_with_retry(
                lambda: fn(*args, **kwargs),
                attempts=attempts, base_delay=base_delay, op_name=op_name,
            )
        return wrapper
    return decorator
