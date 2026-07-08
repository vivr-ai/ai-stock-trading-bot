"""Confirms the shared retry helper actually retries transient failures and
gives up (raising) after exhausting attempts, rather than looping forever."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from bot.utils.retry import call_with_retry


def test_succeeds_after_transient_failures():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        return "ok"

    result = call_with_retry(flaky, attempts=5, base_delay=0.001)
    assert result == "ok"
    assert calls["n"] == 3


def test_raises_after_exhausting_attempts():
    calls = {"n": 0}

    def always_fails():
        calls["n"] += 1
        raise ConnectionError("still down")

    with pytest.raises(ConnectionError):
        call_with_retry(always_fails, attempts=3, base_delay=0.001)
    assert calls["n"] == 3


def test_first_try_success_does_not_retry():
    calls = {"n": 0}

    def works():
        calls["n"] += 1
        return 42

    assert call_with_retry(works, attempts=5, base_delay=0.001) == 42
    assert calls["n"] == 1
