"""Ingests one item: download, parse, store."""

import asyncio
from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from corpus.ingestion.client import ArchiveOrgClient
from corpus.ingestion.parsing import parse_item
from corpus.ingestion.repository import IngestRepository


@dataclass(frozen=True)
class IngestResult:
    identifier: str
    document_id: int
    pages: int
    """Pages stored."""
    numbered_pages: int
    """Pages with a printed page number."""


class IngestService:
    def __init__(
        self, client: ArchiveOrgClient, sessionmaker: async_sessionmaker[AsyncSession]
    ) -> None:
        self._client = client
        self._sessionmaker = sessionmaker

    async def ingest(self, identifier: str) -> IngestResult:
        """Download and parse the item, then store it in one transaction, replacing any
        earlier copy. The download finishes before a database connection is opened, and
        nothing is written if any step fails."""
        source = await asyncio.to_thread(self._client.fetch, identifier)
        item = parse_item(source)
        async with self._sessionmaker() as session, session.begin():
            document_id = await IngestRepository(session).replace_document(item)
        return IngestResult(
            identifier=identifier,
            document_id=document_id,
            pages=len(item.pages),
            numbered_pages=sum(page.page_number is not None for page in item.pages),
        )

    async def already_ingested(self, identifiers: Collection[str]) -> set[str]:
        """Which of `identifiers` are already stored, so a long run can resume."""
        async with self._sessionmaker() as session:
            return await IngestRepository(session).find_existing(identifiers)
