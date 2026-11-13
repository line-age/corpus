"""Database queries behind search."""

from sqlalchemy.ext.asyncio import AsyncSession


class SearchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
