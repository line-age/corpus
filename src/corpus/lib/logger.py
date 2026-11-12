"""The corpus logger.

    from corpus.lib.logger import logger

    logger.info("indexed %d passages", count, extra={"passages": count})

Everything in corpus logs through this one logger, as JSON lines on stdout, and leaves
collecting the stream to the platform. Each line is one object:

    {"time": "2026-11-12T15:13:21.694Z", "level": "INFO", "logger": "corpus",
     "message": "GET /healthz 200 2.4ms", "method": "GET", "path": "/healthz", ...}

Anything passed as `extra=` becomes a top-level key, so log tools can filter on it. Call
configure_logging() once at startup (create_app does); until then, Python's defaults
apply: WARNING and above, as plain text on stderr.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("corpus")

# Attributes every LogRecord has, so anything else on a record came from `extra=`.
# color_message is an extra uvicorn adds to some records: the message with ANSI colours.
_NOT_EXTRA = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "color_message"}


class JsonFormatter(logging.Formatter):
    """Formats each record as one line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _NOT_EXTRA:
                entry.setdefault(key, value)  # extras never overwrite the fields above
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack"] = self.formatStack(record.stack_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str | int) -> None:
    """Log JSON lines to stdout: corpus records at `level` and above, plus uvicorn's own
    startup, shutdown and error messages. Safe to call more than once: it replaces the
    loggers' handlers rather than adding to them."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    for target in (logger, logging.getLogger("uvicorn")):
        for existing in list(target.handlers):
            target.removeHandler(existing)
        target.addHandler(handler)
    logger.setLevel(level)
