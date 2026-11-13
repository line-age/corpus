import logging
import re

from fastapi.testclient import TestClient

from corpus.api.main import create_app


def _request_logs(caplog, prefix: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "corpus" and r.getMessage().startswith(prefix)]


def test_requests_are_logged_with_status_and_duration(make_client, caplog, configure):
    configure(log_level="INFO")
    make_client().get("/?token=do-not-log")

    [record] = _request_logs(caplog, "GET /")
    assert record.levelno == logging.INFO
    assert re.fullmatch(r"GET / 200 \d+\.\dms", record.getMessage())
    # The same values as separate fields, which the JSON formatter emits as keys.
    assert (record.method, record.path, record.status) == ("GET", "/", 200)
    assert isinstance(record.duration_ms, float)
    assert "do-not-log" not in record.getMessage() + record.path


def test_unhandled_errors_are_logged_as_500_at_error_level(caplog):
    app = create_app()

    @app.get("/_test/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/_test/boom").status_code == 500

    [record] = _request_logs(caplog, "GET /_test/boom")
    assert record.levelno == logging.ERROR
    assert re.fullmatch(r"GET /_test/boom 500 \d+\.\dms", record.getMessage())
    assert record.status == 500
