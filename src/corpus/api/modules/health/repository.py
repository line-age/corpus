"""Database queries behind the health checks.

Each method opens its own connection rather than sharing a session, so the checks can run
concurrently: an AsyncSession must not be used by two tasks at once.
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
