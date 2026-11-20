"""Database queries behind search."""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from corpus.models import Document, Page

LIKE_ESCAPE = "\\"


def _contains_pattern(token: str) -> str:
    """An ILIKE pattern matching `token` anywhere, with its wildcards taken literally."""
    escaped = (
        token.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


@dataclass(frozen=True)
class CorpusStats:
    """What relevance ranking needs to know about the whole corpus."""

    pages: int
    average_page_length: float
    """Mean length of a page's text, in characters."""


class SearchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_pages_containing(self, tokens: Sequence[str]) -> list[Page]:
        """Pages whose text contains every token, ignoring case, each with its document loaded.

        Ordered by publication date, then document, then position in the scan. The trigram
        index on pages.text serves these filters.
        """
        statement = (
            select(Page)
            .join(Page.document)
            .options(contains_eager(Page.document))
            .where(
                *(
                    Page.text.ilike(_contains_pattern(token), escape=LIKE_ESCAPE)
                    for token in tokens
                )
            )
            .order_by(Document.publication_date, Document.id, Page.leaf_number)
        )
        return list((await self._session.scalars(statement)).all())

    async def get_corpus_stats(self) -> CorpusStats:
        """How many pages the corpus has and their average length."""
        statement = select(
            func.count(Page.id), func.coalesce(func.avg(func.length(Page.text)), 0)
        )
        pages, average = (await self._session.execute(statement)).one()
        return CorpusStats(pages=pages, average_page_length=float(average))
