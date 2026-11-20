"""Search logic: finds every occurrence of a phrase in the OCR text and cites it.

The repository narrows the search to pages that contain every word of the query. This
module then matches the whole phrase on each of those pages and cuts, around each match,
the statement that contains it.

- The phrase matches case-insensitively, with any whitespace between its words (OCR
  breaks lines mid-phrase) and whole words only at both ends, so "Martin" does not match
  "Martinez".
- A statement ends at ".", "!" or "?" followed by whitespace, or at a blank line.
  Abbreviations such as "Co." end it early; SNIPPET_CONTEXT keeps long ones bounded.

Results are in corpus order unless ranking is on (the SEARCH_RANKING_ENABLED feature
flag). Ranking scores each page with BM25, taking the whole phrase as the term: a page
scores higher the more often the phrase occurs on it, with diminishing returns, and lower
the longer the page is, so an article about a place outranks a long list that names it
in passing. Every occurrence on a page shares the page's score.
"""

import math
import re
from collections.abc import Sequence

from corpus.api.modules.search.dto import (
    DocumentCitation,
    Order,
    PageCitation,
    SearchHit,
    SearchResponse,
)
from corpus.api.modules.search.repository import CorpusStats, SearchRepository
from corpus.models import Page

SNIPPET_CONTEXT = 200
"""Most characters of context kept on each side of a match."""
ELLIPSIS = "..."

BM25_K1 = 1.2
"""How quickly more occurrences on a page stop adding to its score."""
BM25_B = 0.75
"""How much a page's length counts against it: 0 not at all, 1 fully."""

_CLOSERS = re.escape("\"')]’”")
# The end of a statement before a match, through the whitespace that follows it.
_STATEMENT_BREAK = re.compile(rf"[.!?][{_CLOSERS}]*\s+|\n[ \t]*\n\s*")
# The end of a statement after a match: its punctuation, or the start of a blank line.
_STATEMENT_END = re.compile(rf"(?P<stop>[.!?][{_CLOSERS}]*)(?=\s|\Z)|\n[ \t]*\n")
_WHITESPACE = re.compile(r"\s+")


def phrase_pattern(tokens: Sequence[str]) -> re.Pattern[str]:
    """Matches the tokens in order as whole words, separated by any whitespace."""
    body = r"\s+".join(re.escape(token) for token in tokens)
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


def snippet(text: str, start: int, end: int) -> tuple[str, int, int]:
    """The statement around text[start:end], and where that match lies within it."""
    # Back to the end of the previous statement, or a word boundary within reach.
    lo = max(0, start - SNIPPET_CONTEXT)
    head_cut = False
    previous = None
    for previous in _STATEMENT_BREAK.finditer(text, lo, start):
        pass
    if previous is not None:
        lo = previous.end()
    elif lo > 0:
        head_cut = True
        while lo < start and not text[lo - 1].isspace():
            lo += 1

    # Forward through the end of this statement, or to a word boundary within reach.
    tail_cut = False
    stop = _STATEMENT_END.search(text, end)
    if stop is not None and stop.end() - end <= SNIPPET_CONTEXT:
        hi = stop.end("stop") if stop.group("stop") else stop.start()
    elif len(text) - end <= SNIPPET_CONTEXT:
        hi = len(text)
    else:
        tail_cut = True
        hi = end + SNIPPET_CONTEXT
        while hi > end and not text[hi].isspace():
            hi -= 1
        if hi == end:  # no whitespace within reach; cut mid-word
            hi = end + SNIPPET_CONTEXT

    before = _WHITESPACE.sub(" ", text[lo:start]).lstrip()
    match = _WHITESPACE.sub(" ", text[start:end])
    after = _WHITESPACE.sub(" ", text[end:hi]).rstrip()
    lead = ELLIPSIS if head_cut else ""
    trail = ELLIPSIS if tail_cut else ""
    match_start = len(lead) + len(before)
    return f"{lead}{before}{match}{after}{trail}", match_start, match_start + len(match)


def bm25(
    occurrences: int, page_length: int, matching_pages: int, stats: CorpusStats
) -> float:
    """
    BM25 score of a page on which the phrase occurs `occurrences` times,
    when it occurs on `matching_pages` of the corpus's pages.

    It uses the Okapi BM25 Formula (see https://en.wikipedia.org/wiki/Okapi_BM25), with
    the IDF that Lucene uses, which stays positive however common the phrase is:

        score = IDF * tf * (k1 + 1) / (tf + k1 * (1 - b + b * L / L_avg))
        IDF   = ln(1 + (N - df + 0.5) / (df + 0.5))

    where
        tf     `occurrences`: times the phrase occurs on this page
        L      `page_length`: length of this page's text, in characters
        L_avg  `stats.average_page_length`: mean page length across the corpus
        N      `stats.pages`: pages in the corpus
        df     `matching_pages`: pages on which the phrase occurs at least once
        k1     BM25_K1: how quickly more occurrences stop adding to the score
        b      BM25_B: how much page length counts against a page

    The whole phrase is the one term, so IDF is the same for every page in a query and
    only tf and L change the order. When L_avg is 0 (every page empty), L / L_avg is
    taken as 1.
    """
    idf = math.log(1 + (stats.pages - matching_pages + 0.5) / (matching_pages + 0.5))
    relative_length = (
        page_length / stats.average_page_length if stats.average_page_length else 1
    )
    saturation = BM25_K1 * (1 - BM25_B + BM25_B * relative_length)
    return idf * occurrences * (BM25_K1 + 1) / (occurrences + saturation)


# An occurrence: its page, where it starts and ends in the page's text, and its score.
Occurrence = tuple[Page, int, int, float | None]


class SearchService:
    def __init__(self, repository: SearchRepository, *, ranking: bool = False) -> None:
        self._repository = repository
        self._ranking = ranking

    async def search(self, query: str, limit: int, offset: int) -> SearchResponse:
        """Every occurrence of `query`, with `limit` of them from `offset`: in corpus order,
        or by relevance when ranking is on.

        Logic:
        - Split search query into tokens
        -  SQL filters to pages containing every word
        - Then the regex matches the exact phrase.
        - Aggregate documents containing tokens
        - Rank documents by relevance with bm25 if ranking is turned on
        - Return occurrences and metadata in search response

        """
        tokens = query.split()

        matches: list[tuple[Page, list[re.Match[str]]]] = []
        if tokens:
            phrase = phrase_pattern(
                tokens
            )  # equivalent of what phraseto_ts_query in Postgres would do

            # Find all pages containing of the search tokens in the database
            # Perform regex match for the search phrase
            # Add the page and matched phrases to the matches list
            for page in await self._repository.find_pages_containing(tokens):
                if found := list(phrase.finditer(page.text)):
                    matches.append((page, found))

        order: Order = "corpus"
        occurrences: list[Occurrence]
        if self._ranking:
            order = "relevance"
            occurrences = await self._ranked(matches)
        else:
            occurrences = [
                (page, m.start(), m.end(), None)
                for page, found in matches
                for m in found
            ]
        return SearchResponse(
            query=query,
            order=order,
            total=len(occurrences),
            limit=limit,
            offset=offset,
            results=[
                _hit(*occurrence) for occurrence in occurrences[offset : offset + limit]
            ],
        )

    async def _ranked(
        self, matches: list[tuple[Page, list[re.Match[str]]]]
    ) -> list[Occurrence]:
        """The occurrences, highest-scoring page first. The sort is stable, so pages with
        equal scores, and occurrences on the same page, stay in corpus order."""
        if not matches:
            return []
        stats = await self._repository.get_corpus_stats()
        scored = [
            (page, found, bm25(len(found), len(page.text), len(matches), stats))
            for page, found in matches
        ]
        scored.sort(key=lambda entry: entry[2], reverse=True)
        return [
            (page, m.start(), m.end(), round(score, 4))
            for page, found, score in scored
            for m in found
        ]


def _hit(page: Page, start: int, end: int, score: float | None) -> SearchHit:
    document = page.document
    text, match_start, match_end = snippet(page.text, start, end)
    return SearchHit(
        document=DocumentCitation(
            identifier=document.identifier,
            title=document.title,
            publication_date=document.publication_date,
            volume=document.volume,
            issue=document.issue,
            source_url=document.source_url,
        ),
        page=PageCitation(
            leaf_number=page.leaf_number,
            page_number=page.page_number,
            url=f"{document.source_url.rstrip('/')}/page/n{page.leaf_number}",
        ),
        snippet=text,
        match_start=match_start,
        match_end=match_end,
        score=score,
    )
