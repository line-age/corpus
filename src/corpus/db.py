"""Database engine, sessions and ORM base, shared by the search API and (later) ingest.

Endpoints get a session by dependency injection:

    from typing import Annotated

    from fastapi import Depends
    from sqlalchemy.ext.asyncio import AsyncSession

    from corpus.db import get_session

    @router.get("/things")
    async def list_things(session: Annotated[AsyncSession, Depends(get_session)]): ...

The engine and sessionmaker belong to the app (created at startup, disposed at shutdown)
rather than to this module, so each app, including each one a test builds, uses its own
settings and its own connection pool.
"""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from corpus.config import Settings


class Base(DeclarativeBase):
    """Base class for ORM models. Its metadata is what Alembic autogenerate compares against."""
    pass


def make_engine(settings: Settings) -> AsyncEngine:
    """Create an async engine. No connection is opened until first use."""
    return create_async_engine(
        settings.database_url,
        pool_size=settings.postgres_pool_size,
        max_overflow=settings.postgres_max_connections - settings.postgres_pool_size,
        pool_pre_ping=True,
        connect_args={"connect_timeout": settings.postgres_connect_timeout},
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory for `engine`. Objects stay readable after commit."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, closed after the response.

    Commit explicitly. Anything left uncommitted is rolled back when the session closes.
    """
    async with request.app.state.sessionmaker() as session:
        yield session
