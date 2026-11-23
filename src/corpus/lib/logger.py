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

Records made while request_id_var is set, which RequestIdMiddleware does for each HTTP
request, also get a `request_id` key, without the caller passing it.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
"""The ID of the request being handled, or None outside a request. Each request runs in
its own asyncio task, which has its own copy of this value, so concurrent requests never
see each other's IDs."""


class _RequestIdFilter(logging.Filter):
    """Adds the current request ID to each record. It is a logger filter rather than part
    of the formatter so that the field is on the record itself, where pytest's caplog and
    any other handler see it too."""

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = request_id_var.get()
        if request_id is not None and not hasattr(record, "request_id"):
            record.request_id = request_id  # an explicit extra={"request_id": ...} wins
        return True


logger = logging.getLogger("corpus")
logger.addFilter(_RequestIdFilter())

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
