"""Logging setup: plain text for a human at a terminal, structured JSON for
a log aggregator (Railway, Datadog, etc.).

Toggle with LOG_FORMAT=text|json (config.logging.log_format). Every scan and
trade-decision log call in bot/trading/strategy.py passes structured fields
via the stdlib logging `extra=` mechanism; the JSON formatter below promotes
those fields into the output object, so in JSON mode each log line is a
queryable record: {"symbol": "AAPL", "decision": "buy", "sentiment_score": 8.5,
...} instead of only free text. In text mode the same calls still render as
readable sentences — the structured fields are just not the primary content.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

# Attributes every stdlib LogRecord has out of the box; anything else set on
# a record (via `extra=`) is a field *we* added and should be promoted into
# the JSON payload.
_STANDARD_RECORD_KEYS = frozenset(logging.LogRecord(
    "", 0, "", 0, "", (), None
).__dict__.keys()) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_KEYS:
                continue
            try:
                json.dumps(value)  # only include JSON-serializable extras
                payload[key] = value
            except TypeError:
                payload[key] = str(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Same human-readable shape the bot has always used, unchanged."""

    def __init__(self):
        super().__init__(
            fmt="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        )


def setup_logging(level: str, run_log_path: str, log_format: str = "text") -> None:
    formatter = JsonFormatter() if log_format == "json" else TextFormatter()

    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        import os
        directory = os.path.dirname(os.path.abspath(run_log_path))
        os.makedirs(directory, exist_ok=True)
        handlers.append(logging.FileHandler(run_log_path))
    except OSError as exc:
        logging.getLogger("bot.logging_setup").warning(
            "Could not open run log file %s (%s); logging to stdout only.",
            run_log_path, exc,
        )

    for h in handlers:
        h.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))
    root.handlers = handlers  # replace, don't stack, in case of re-init
