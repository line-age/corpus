"""Database queries behind the health checks.

- Each method opens its own connection because HealthService runs the checks concurrently,
  and concurrent tasks can't share a connection or an AsyncSession.
- The database and pgvector checks are separate so /healthz says which problem to fix:
  "database" fails when Postgres can't be reached, "pgvector" when it can but the vector
  extension isn't installed (the database hasn't been migrated).
- get_pgvector_version() alone would prove the database is reachable. ping() exists so each
  check is independent, with its own timeout and latency.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class HealthRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ping(self) -> None:
        """Run a trivial query, raising if the database cannot be reached."""
        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def get_pgvector_version(self) -> str | None:
        """The installed version of the vector extension, or None if it is not installed."""
        async with self._engine.connect() as conn:
            return await conn.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
