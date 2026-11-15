import json
import logging
import sys

from corpus.lib.logger import JsonFormatter, configure_logging, logger


def _format(**kwargs) -> dict:
    record = logger.makeRecord(
        "corpus", logging.INFO, __file__, 1, "hello %s", ("world",), None, **kwargs
    )
    return json.loads(JsonFormatter().format(record))


def test_records_are_one_json_object_with_the_standard_fields():
    entry = _format()

    assert entry["level"] == "INFO"
    assert entry["logger"] == "corpus"
    assert entry["message"] == "hello world"
    assert entry["time"].endswith("Z")


def test_extra_fields_become_top_level_keys_without_overwriting_standard_ones():
    entry = _format(extra={"path": "/healthz", "status": 200, "level": "spoofed"})

    assert entry["path"] == "/healthz"
    assert entry["status"] == 200
    assert entry["level"] == "INFO"


def test_exceptions_are_included_as_a_traceback_string():
    try:
        raise ValueError("boom")
    except ValueError:
        record = logger.makeRecord(
            "corpus", logging.ERROR, __file__, 1, "failed", (), exc_info=sys.exc_info()
        )

    entry = json.loads(JsonFormatter().format(record))

    assert "ValueError: boom" in entry["exception"]


def test_configure_logging_replaces_handlers_instead_of_adding_more():
    uvicorn = logging.getLogger("uvicorn")
    try:
        configure_logging("INFO")
        configure_logging("DEBUG")

        assert len(logger.handlers) == 1
        assert logger.level == logging.DEBUG
        assert [type(h.formatter) for h in uvicorn.handlers] == [JsonFormatter]
    finally:
        configure_logging("INFO")
