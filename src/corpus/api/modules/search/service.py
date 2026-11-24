"""Search logic: cites each passage that pg_search finds for a phrase.

pg_search does the matching and ranking in the database (see the repository). It finds
the passages that contain the query as a phrase and ranks them by BM25: a passage scores
higher the more often the query's words occur in it, with diminishing returns, the rarer
those words are across the corpus, and the shorter the passage is. This module only turns
each passage into a citation with a snippet for each occurrence of the phrase.

- One result per passage. Passages overlap by 50 words, so a phrase in an overlap can be
  cited twice, once from each passage.
- Snippets are cut from the passage's normalised text (whitespace collapsed, words
  hyphenated across a line break rejoined), which is the text the index matched.
- Where the phrase is comes from pg_search too: the query's words as the index splits
  them, and where each of them occurs in the passage. This module only picks out the
  places those words occur together, in order.
- A statement ends at ".", "!" or "?" followed by whitespace. Abbreviations such as "Co."
  end it early; SNIPPET_CONTEXT keeps long ones bounded.
"""

import re
from collections.abc import Sequence
from itertools import pairwise

from corpus.api.modules.search.dto import (
    DocumentCitation,
    PageCitation,
    SearchHit,
    SearchResponse,
    Snippet,
)
from corpus.api.modules.search.repository import PassageMatch, SearchRepository, Span
from corpus.lib.logger import logger

SNIPPET_CONTEXT = 200
"""Most characters of context kept on each side of a match."""
ELLIPSIS = "..."

_WORD_CHARACTER = re.compile(r"\w")

_CLOSERS = re.escape("\"')]’”")
# The end of a statement before a match, through the whitespace that follows it.
_STATEMENT_BREAK = re.compile(rf"[.!?][{_CLOSERS}]*\s+|\n[ \t]*\n\s*")
# The end of a statement after a match: its punctuation, or the start of a blank line.
_STATEMENT_END = re.compile(rf"(?P<stop>[.!?][{_CLOSERS}]*)(?=\s|\Z)|\n[ \t]*\n")
_WHITESPACE = re.compile(r"\s+")


def find_phrases(
    text: str, word_spans: Sequence[Span], terms: Sequence[str]
) -> list[Span]:
    """Every place `terms` occur in `text` one after another, as (start, end) offsets into
    `text`, in order.

    `word_spans` are where pg_search found the query's words in `text`, in order. It marks
    every occurrence of every word, so an occurrence of the phrase is a run of spans that
    spells `terms` in order with no other word between them. Punctuation between them is
    ignored, as pg_search ignores it. Occurrences don't overlap: after one is found, the
    search resumes past it.
    """
    size = len(terms)
    found: list[Span] = []
    if not size:
        return found
    i = 0
    while i <= len(word_spans) - size:
        run = word_spans[i : i + size]
        spells_terms = all(
            text[start:end].lower() == term
            for (start, end), term in zip(run, terms, strict=True)
        )
        adjacent = not any(
            _WORD_CHARACTER.search(text, previous_end, next_start)
            for (_, previous_end), (next_start, _) in pairwise(run)
        )
        if spells_terms and adjacent:
            found.append((run[0][0], run[-1][1]))
            i += size
        else:
            i += 1
    return found


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


class SearchService:
    def __init__(
        self, repository: SearchRepository, *, archive_org_base_url: str
    ) -> None:
        self._repository = repository
        self._archive_org_base_url = archive_org_base_url.rstrip("/")

    async def search(self, query: str, limit: int, offset: int) -> SearchResponse:
        """The passages containing `query` as a phrase, most relevant first, `limit` of them
        from `offset`, each cited with a snippet around every occurrence of the phrase."""
        terms = await self._repository.query_terms(query)
        total = 0
        matches: list[PassageMatch] = []
        # A query with no words (only punctuation) can match nothing,
        # so we skip the count and the search.
        if terms:
            total = await self._repository.count_passages(query)
            if offset < total:
                matches = await self._repository.find_passages(query, limit, offset)
        return SearchResponse(
            query=query,
            total=total,
            limit=limit,
            offset=offset,
            results=[self._hit(match, terms) for match in matches],
        )

    def _hit(self, match: PassageMatch, terms: Sequence[str]) -> SearchHit:
        found = find_phrases(match.search_text, match.word_spans, terms)
        if not found:
            # The index matched the passage, but its word positions don't spell the phrase.
            # Cite the start of the passage rather than drop the result.
            logger.warning(
                "search match not located in passage",
                extra={"identifier": match.identifier, "leaf": match.leaf},
            )
            found = [(0, 0)]
        snippets = [
            Snippet(text=text, match_start=match_start, match_end=match_end)
            for text, match_start, match_end in (
                snippet(match.search_text, start, end) for start, end in found
            )
        ]
        source_url = f"{self._archive_org_base_url}/details/{match.identifier}"
        return SearchHit(
            document=DocumentCitation(
                identifier=match.identifier,
                title=match.title,
                publication_date=match.issue_date,
                volume=match.volume,
                issue=match.issue,
                source_url=source_url,
            ),
            page=PageCitation(
                leaf_number=match.leaf,
                page_number=match.printed_page,
                url=f"{source_url}/page/n{match.leaf}",
            ),
            snippets=snippets,
            score=round(match.score, 4),
        )
