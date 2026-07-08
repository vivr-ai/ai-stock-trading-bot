"""Confirms structured (JSON) log records carry the extra fields passed via
logging's `extra=`, which is how strategy.py attaches symbol/decision/reason
to every scan and trade decision."""
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.utils.logging_setup import JsonFormatter


def test_json_formatter_includes_extra_fields():
    formatter = JsonFormatter()
    logger = logging.getLogger("test.json")
    record = logger.makeRecord(
        "test.json", logging.INFO, __file__, 1, "AAPL score=8.5", (), None,
        extra={"symbol": "AAPL", "decision": "buy", "sentiment_score": 8.5},
    )
    out = json.loads(formatter.format(record))
    assert out["symbol"] == "AAPL"
    assert out["decision"] == "buy"
    assert out["sentiment_score"] == 8.5
    assert out["message"] == "AAPL score=8.5"
    assert out["level"] == "INFO"
