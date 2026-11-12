"""Runs the health checks and summarises the result.

Each check runs concurrently under a timeout. Failure details are deliberately terse (no
hostnames or driver messages); the full error is logged.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

from corpus import __version__
from corpus.api.modules.health.dto import CheckResult, HealthResponse, Status
from corpus.api.modules.health.exceptions import CheckFailed
from corpus.api.modules.health.repository import HealthRepository
from corpus.lib.logger import logger

# A check returns an optional detail on success and raises on failure.
Check = Callable[[], Awaitable[str | None]]


class HealthService:
    def __init__(self, repository: HealthRepository, timeout: float) -> None:
        self._repository = repository
        self._timeout = timeout

    async def check(self) -> HealthResponse:
        checks: dict[str, Check] = {
            "database": self._check_database,
            "pgvector": self._check_pgvector,
        }
        results = await asyncio.gather(*(self._run(name, check) for name, check in checks.items()))
        by_name = dict(zip(checks, results, strict=True))
        healthy = all(result.status == "ok" for result in by_name.values())
        return HealthResponse(
            status="ok" if healthy else "error", version=__version__, checks=by_name
        )

    async def _check_database(self) -> str | None:
        await self._repository.ping()
        return None

    async def _check_pgvector(self) -> str | None:
        version = await self._repository.get_pgvector_version()
        if version is None:
            raise CheckFailed("vector extension is not installed; run the migrations")
        return f"vector {version}"

    async def _run(self, name: str, check: Check) -> CheckResult:
        start = time.perf_counter()

        def result(state: Status, detail: str | None) -> CheckResult:
            latency = round((time.perf_counter() - start) * 1000, 2)
            return CheckResult(status=state, latency_ms=latency, detail=detail)

        try:
            async with asyncio.timeout(self._timeout):
                detail = await check()
        except CheckFailed as exc:
            return result("error", str(exc))
        except TimeoutError:
            logger.warning(
                "health check %r timed out after %ss", name, self._timeout, extra={"check": name}
            )
            return result("error", f"timed out after {self._timeout:g}s")
        except Exception as exc:
            logger.warning("health check %r failed", name, exc_info=True, extra={"check": name})
            return result("error", type(exc).__name__)
        return result("ok", detail)
