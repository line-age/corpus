"""HealthService against fake repositories, for failures a real database can't easily produce."""

import asyncio

from corpus.api.modules.health.service import HealthService


class SlowDatabase:
    async def ping(self) -> None:
        await asyncio.sleep(1)

    async def get_pgvector_version(self) -> str | None:
        return "0.0.0"


class BrokenDatabase:
    async def ping(self) -> None:
        raise ConnectionError("could not connect to db.internal:5432")

    async def get_pgvector_version(self) -> str | None:
        raise ConnectionError("could not connect to db.internal:5432")


def test_a_slow_check_times_out_without_failing_the_others():
    result = asyncio.run(HealthService(SlowDatabase(), timeout=0.05).check())

    assert result.status == "error"
    assert result.checks["database"].status == "error"
    assert result.checks["database"].detail == "timed out after 0.05s"
    assert result.checks["pgvector"].status == "ok"


def test_unexpected_errors_are_reported_by_type_only():
    result = asyncio.run(HealthService(BrokenDatabase(), timeout=1).check())

    assert result.checks["database"].detail == "ConnectionError"
    assert "db.internal" not in result.model_dump_json()
