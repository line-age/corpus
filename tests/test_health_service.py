"""HealthService against a mocked repository, for failures a real database can't easily produce."""

import asyncio
from unittest.mock import create_autospec

import pytest

from corpus.api.modules.health.repository import HealthRepository, SearchIndexStatus
from corpus.api.modules.health.service import HealthService


@pytest.fixture
def repository():
    """A HealthRepository mock whose checks all pass. Autospec limits it to the real class's
    methods and signatures, so a renamed or removed method fails the test."""
    mock = create_autospec(HealthRepository, instance=True)
    mock.ping.return_value = None
    mock.get_search_index_status.return_value = SearchIndexStatus(
        pg_search_version="0.0.0", index_exists=True
    )
    return mock


def test_a_slow_check_times_out_without_failing_the_others(repository):
    async def slow_ping() -> None:
        await asyncio.sleep(1)

    repository.ping.side_effect = slow_ping

    result = asyncio.run(HealthService(repository, timeout=0.05).check())

    assert result.status == "error"
    assert result.checks["database"].status == "error"
    assert result.checks["database"].detail == "timed out after 0.05s"
    assert result.checks["pg_search"].status == "ok"


def test_unexpected_errors_are_reported_by_type_only(repository):
    repository.ping.side_effect = ConnectionError(
        "could not connect to db.internal:5432"
    )
    repository.get_search_index_status.side_effect = ConnectionError(
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


def test_search_index_check_reports_the_pg_search_version(repository):
    result = asyncio.run(HealthService(repository, timeout=1).check())

    assert result.status == "ok"
    assert result.checks["pg_search"].detail == "pg_search 0.0.0"


def test_search_index_check_fails_without_pg_search(repository):
    repository.get_search_index_status.return_value = SearchIndexStatus(
        pg_search_version=None, index_exists=False
    )

    result = asyncio.run(HealthService(repository, timeout=1).check())

    assert result.status == "error"
    assert result.checks["pg_search"].detail == (
        "pg_search extension is not installed; run the migrations"
    )


def test_search_index_check_fails_without_the_bm25_index(repository):
    repository.get_search_index_status.return_value = SearchIndexStatus(
        pg_search_version="0.0.0", index_exists=False
    )

    result = asyncio.run(HealthService(repository, timeout=1).check())

    assert result.status == "error"
    assert (
        result.checks["pg_search"].detail
        == "search index is missing; run the migrations"
    )
