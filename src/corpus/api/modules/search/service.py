"""Search logic: finds every occurrence of a phrase in the OCR text and cites it.

The repository narrows the search to pages that contain every word of the query. This
module then matches the whole phrase on each of those pages and cuts, around each match,
the statement that contains it.

- The phrase matches case-insensitively, with any whitespace between its words (OCR
  breaks lines mid-phrase) and whole words only at both ends, so "Martin" does not match
  "Martinez".
- A statement ends at ".", "!" or "?" followed by whitespace, or at a blank line.
  Abbreviations such as "Co." end it early; SNIPPET_CONTEXT keeps long ones bounded.
"""

import re
from collections.abc import Sequence

from corpus.api.modules.search.dto import DocumentCitation, PageCitation, SearchHit, SearchResponse
from corpus.api.modules.search.repository import SearchRepository
from corpus.models import Page

SNIPPET_CONTEXT = 200
"""Most characters of context kept on each side of a match."""
ELLIPSIS = "..."

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


class SearchService:
    def __init__(self, repository: SearchRepository) -> None:
        self._repository = repository

    async def search(self, query: str, limit: int, offset: int) -> SearchResponse:
        """Every occurrence of `query`, in corpus order, with `limit` of them from `offset`."""
        tokens = query.split()
        occurrences: list[tuple[Page, int, int]] = []
        if tokens:
            phrase = phrase_pattern(tokens)
            for page in await self._repository.find_pages_containing(tokens):
                occurrences.extend((page, m.start(), m.end()) for m in phrase.finditer(page.text))
        return SearchResponse(
            query=query,
            total=len(occurrences),
            limit=limit,
            offset=offset,
            results=[_hit(*occurrence) for occurrence in occurrences[offset : offset + limit]],
        )


def _hit(page: Page, start: int, end: int) -> SearchHit:
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
    )
