"""Database queries behind the health checks.

- Each method opens its own connection because HealthService runs the checks concurrently,
  and concurrent tasks can't share a connection or an AsyncSession.
- The database and search-index checks are separate so /healthz says which problem to fix:
  "database" fails when Postgres can't be reached, "pg_search" when it can but search
  can't run (the extension or the BM25 index is missing; the database hasn't been migrated).
- get_search_index_status() alone would prove the database is reachable. ping() exists so
  each check is independent, with its own timeout and latency.
"""

from dataclasses import dataclass

from corpus_core.db import BM25_INDEX
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class SearchIndexStatus:
    pg_search_version: str | None
    """The installed version of the pg_search extension, or None if it is not installed."""
    index_exists: bool
    """Whether the BM25 index over passages exists."""


class HealthRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ping(self) -> None:
        """Run a trivial query, raising if the database cannot be reached."""
        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def get_search_index_status(self) -> SearchIndexStatus:
        """Whether pg_search is installed, and whether the BM25 index over passages exists."""
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT"
                        " (SELECT extversion FROM pg_extension WHERE extname = 'pg_search'),"
                        " to_regclass(:index) IS NOT NULL"
                    ),
                    {"index": BM25_INDEX},
                )
            ).one()
        return SearchIndexStatus(pg_search_version=row[0], index_exists=row[1])
