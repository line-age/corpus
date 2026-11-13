"""HealthService against a mocked repository, for failures a real database can't easily produce."""

import asyncio
from unittest.mock import create_autospec

import pytest

from corpus.api.modules.health.repository import HealthRepository
from corpus.api.modules.health.service import HealthService


@pytest.fixture
def repository():
    """A HealthRepository mock whose checks all pass. Autospec limits it to the real class's
    methods and signatures, so a renamed or removed method fails the test."""
    mock = create_autospec(HealthRepository, instance=True)
    mock.ping.return_value = None
    mock.get_pgvector_version.return_value = "0.0.0"
    return mock


def test_a_slow_check_times_out_without_failing_the_others(repository):
    async def slow_ping() -> None:
        await asyncio.sleep(1)

    repository.ping.side_effect = slow_ping

    result = asyncio.run(HealthService(repository, timeout=0.05).check())

    assert result.status == "error"
    assert result.checks["database"].status == "error"
    assert result.checks["database"].detail == "timed out after 0.05s"
    assert result.checks["pgvector"].status == "ok"


def test_unexpected_errors_are_reported_by_type_only(repository):
    repository.ping.side_effect = ConnectionError("could not connect to db.internal:5432")
    repository.get_pgvector_version.side_effect = ConnectionError(
        "could not connect to db.internal:5432"
    )

    result = asyncio.run(HealthService(repository, timeout=1).check())

    assert result.checks["database"].detail == "ConnectionError"
    assert "db.internal" not in result.model_dump_json()


def test_a_timeout_raised_inside_a_check_is_not_reported_as_the_deadline(repository):
    # TimeoutError is also what sockets and nested asyncio timeouts raise.
    repository.ping.side_effect = TimeoutError("socket timed out")

    result = asyncio.run(HealthService(repository, timeout=3).check())

    assert result.checks["database"].status == "error"
    assert result.checks["database"].detail == "TimeoutError"
