"""Database queries behind search.

pg_search does the matching and ranking. `###` matches the query as a phrase, tokenized
by the passage_bm25 index's tokenizer, and pdb.score() is each passage's BM25 score.
Sorting and paging by that score happen in the same query. pdb.snippet_positions() says
where the query's words are in each passage, so the service can cite the match without
splitting text into words itself.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from corpus_core.db import item, page, passage
from sqlalchemy import ColumnElement, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

# Splits text into words as passage_bm25 does. The index sets no tokenizer, so it uses
# pg_search's default, unicode_words; change both together.
_QUERY_TERMS = text("SELECT CAST(CAST(:query AS text) AS pdb.unicode_words)::text[]")

Span = tuple[int, int]


@dataclass(frozen=True)
class PassageMatch:
    """A passage that matches the query, with what its citation needs."""

    identifier: str
    title: str
    issue_date: date | None
    volume: str | None
    issue: str | None
    leaf: int
    """0-based position of the passage's page in the scan."""
    printed_page: str | None
    search_text: str
    """The passage's normalised text, as the index holds it."""
    score: float
    """BM25 relevance of the passage to the query; higher is more relevant."""
    word_spans: tuple[Span, ...]
    """Where the query's words occur in `search_text`, as (start, end) character offsets,
    in order. Every occurrence of every word is included, not only those in the phrase."""


def _matches(query: str) -> ColumnElement[bool]:
    """Passages containing `query` as a phrase. The query is a bound parameter."""
    return passage.c.search_text.op("###")(query)


def _char_spans(text: str, byte_spans: Sequence[Sequence[int]] | None) -> tuple[Span, ...]:
    """pg_search reports positions as UTF-8 byte offsets; Python indexes by character."""
    if not byte_spans:
        return ()
    if text.isascii():
        return tuple((start, end) for start, end in byte_spans)
    encoded = text.encode()

    def chars(offset: int) -> int:
        # Offsets fall between characters; "ignore" keeps a stray one from failing a search.
        return len(encoded[:offset].decode(errors="ignore"))

    return tuple((chars(start), chars(end)) for start, end in byte_spans)


class SearchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def query_terms(self, query: str) -> list[str]:
        """The words of `query`, lower-cased, as the search index splits text into words."""
        return list(await self._session.scalar(_QUERY_TERMS, {"query": query}) or [])

    async def count_passages(self, query: str) -> int:
        """How many passages contain the phrase."""
        statement = select(func.count()).select_from(passage).where(_matches(query))
        return await self._session.scalar(statement) or 0

    async def find_passages(self, query: str, limit: int, offset: int) -> list[PassageMatch]:
        """The passages containing the phrase, highest BM25 score first, `limit` of them
        from `offset`. Ties are broken by passage id, so paging is stable."""
        score = func.pdb.score(passage.c.id).label("score")

        # Alternative implementation idea:
        # Rewrite this query to use top-K subquery to narrow down the 
        # the result set with the `where` filter before joining on other tables.
        # This would ensure that postgres doesn't join every matching row before applying
        # the `limit` on the number of rows to return. Becomes useful when the dataset is 
        # larger.
        # (Explore caveats and justify upside before implementing.)
        statement = (
            select(
                passage.c.identifier,
                item.c.title,
                item.c.issue_date,
                item.c.volume,
                item.c.issue,
                passage.c.leaf,
                page.c.printed_page,
                passage.c.search_text,
                score,
                func.pdb.snippet_positions(passage.c.search_text).label("positions"),
            )
            .join(item, item.c.identifier == passage.c.identifier)
            .join(
                page,
                (page.c.identifier == passage.c.identifier) & (page.c.leaf == passage.c.leaf),
            )
            .where(_matches(query))
            .order_by(score.desc(), passage.c.id)
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(statement)).mappings()
        return [
            PassageMatch(
                identifier=row["identifier"],
                title=row["title"],
                issue_date=row["issue_date"],
                volume=row["volume"],
                issue=row["issue"],
                leaf=row["leaf"],
                printed_page=row["printed_page"],
                search_text=row["search_text"],
                score=float(row["score"]),
                word_spans=_char_spans(row["search_text"], row["positions"]),
            )
            for row in rows
        ]
