"""Writes ingested documents and their pages."""

from collections.abc import Collection

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from corpus.ingestion.parsing import ParsedItem
from corpus.models import Document, Page


class IngestRepository:
    """Queries only. The caller owns the transaction and commits it."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_document(self, item: ParsedItem) -> int:
        """Store the document and its pages, replacing any earlier copy with the same
        identifier, and return the document's id.

        The document row is updated in place, so its id stays the same across
        re-ingests, and its pages are deleted and inserted again. Upserting locks the
        row, so two ingests of the same item run one after the other.
        """
        fields = vars(item.document)
        upsert = pg_insert(Document).values(**fields)
        upsert = upsert.on_conflict_do_update(
            index_elements=[Document.identifier],
            set_={
                **{
                    name: upsert.excluded[name]
                    for name in fields
                    if name != "identifier"
                },
                "ingested_at": func.now(),
            },
        ).returning(Document.id)
        document_id = (await self._session.execute(upsert)).scalar_one()

        await self._session.execute(delete(Page).where(Page.document_id == document_id))
        if item.pages:
            await self._session.execute(
                insert(Page),
                [{"document_id": document_id, **vars(page)} for page in item.pages],
            )
        return document_id

    async def find_existing(self, identifiers: Collection[str]) -> set[str]:
        """Which of `identifiers` are already stored."""
        if not identifiers:
            return set()
        statement = select(Document.identifier).where(
            Document.identifier.in_(identifiers)
        )
        return set((await self._session.scalars(statement)).all())
