import asyncio
import logging
import uuid

import httpx
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from corpus.api.main import create_app
from corpus.lib.logger import logger, request_id_var


def _logs(caplog, prefix: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "corpus" and r.getMessage().startswith(prefix)]


def _is_uuid4(value: str) -> bool:
    try:
        return str(uuid.UUID(value, version=4)) == value
    except ValueError:
        return False


def test_the_callers_request_id_is_returned_and_logged(make_client, caplog, configure):
    configure(log_level="INFO")
    response = make_client().get("/", headers={"X-Request-ID": "abc-123.x:y_z"})

    assert response.headers["x-request-id"] == "abc-123.x:y_z"
    [record] = _logs(caplog, "GET /")
    assert record.request_id == "abc-123.x:y_z"


def test_a_uuid_is_generated_when_the_caller_sends_none(make_client, caplog, configure):
    configure(log_level="INFO")
    response = make_client().get("/")

    request_id = response.headers["x-request-id"]
    assert _is_uuid4(request_id)
    [record] = _logs(caplog, "GET /")
    assert record.request_id == request_id


def test_each_request_without_an_id_gets_a_different_one(make_client):
    client = make_client()

    assert client.get("/").headers["x-request-id"] != client.get("/").headers["x-request-id"]


@pytest.mark.parametrize("bad_id", ["", "has space", "semi;colon", "x" * 129, "<script>"])
def test_a_malformed_request_id_is_replaced_and_never_logged(
    make_client, caplog, configure, bad_id
):
    configure(log_level="INFO")
    response = make_client().get("/", headers={"X-Request-ID": bad_id})

    request_id = response.headers["x-request-id"]
    assert _is_uuid4(request_id)
    [record] = _logs(caplog, "GET /")
    assert record.request_id == request_id
    if bad_id:
        assert all(bad_id not in str(vars(r)) for r in caplog.records)


def test_logs_made_by_handlers_carry_the_request_id(caplog, configure):
    configure(log_level="INFO")
    app = create_app()

    @app.get("/_test/async")
    async def async_handler(request: Request) -> str:
        logger.info("in async handler")
        return request.state.request_id

    @app.get("/_test/sync")
    def sync_handler(request: Request) -> str:  # runs in the threadpool
        logger.info("in sync handler")
        return request.state.request_id

    with TestClient(app) as client:
        for kind in ("async", "sync"):
            response = client.get(f"/_test/{kind}", headers={"X-Request-ID": f"id-{kind}"})

            assert response.json() == f"id-{kind}"
            [record] = _logs(caplog, f"in {kind} handler")
            assert record.request_id == f"id-{kind}"


def test_unhandled_errors_are_logged_with_the_request_id(caplog):
    app = create_app()

    @app.get("/_test/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/_test/boom", headers={"X-Request-ID": "boom-1"}).status_code == 500

    [record] = _logs(caplog, "GET /_test/boom")
    assert record.request_id == "boom-1"


async def test_concurrent_requests_keep_their_own_ids(caplog, configure):
    configure(log_level="INFO")
    app = create_app()
    both_in_flight = asyncio.Barrier(2)

    @app.get("/_test/wait")
    async def wait() -> str | None:
        await both_in_flight.wait()  # each request now runs while the other is inside too
        logger.info("after barrier")
        return request_id_var.get()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first, second = await asyncio.wait_for(
            asyncio.gather(
                client.get("/_test/wait", headers={"X-Request-ID": "first"}),
                client.get("/_test/wait", headers={"X-Request-ID": "second"}),
            ),
            timeout=5,
        )

    assert (first.json(), second.json()) == ("first", "second")
    assert sorted(r.request_id for r in _logs(caplog, "after barrier")) == ["first", "second"]
    assert request_id_var.get() is None
