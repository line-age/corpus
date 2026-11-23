"""Search logic: cites each passage that pg_search finds for a phrase.

pg_search does the matching and ranking in the database (see the repository). It finds
the passages that contain the query as a phrase and ranks them by BM25: a passage scores
higher the more often the query's words occur in it, with diminishing returns, the rarer
those words are across the corpus, and the shorter the passage is. This module only turns
each passage into a citation with a snippet.

- One result per passage. Passages overlap by 50 words, so a phrase in an overlap can be
  cited twice, once from each passage.
- The snippet is cut from the passage's normalised text (whitespace collapsed, words
  hyphenated across a line break rejoined), which is the text the index matched.
- A statement ends at ".", "!" or "?" followed by whitespace. Abbreviations such as "Co."
  end it early; SNIPPET_CONTEXT keeps long ones bounded.
"""

import re
from collections.abc import Sequence

from corpus.api.modules.search.dto import (
    DocumentCitation,
    PageCitation,
    SearchHit,
    SearchResponse,
)
from corpus.api.modules.search.repository import PassageMatch, SearchRepository
from corpus.lib.logger import logger

SNIPPET_CONTEXT = 200
"""Most characters of context kept on each side of a match."""
ELLIPSIS = "..."

# A word as pg_search's default tokenizer (unicode_words, Unicode word boundaries) sees
# it: letters, digits and underscores, kept together across an apostrophe or full stop
# between letters or digits ("O'Brien", "3.5"), a colon between letters, and a comma or
# semicolon between digits ("1,000"). Hyphens and other punctuation separate words.
_TOKEN = re.compile(
    r"\w+(?:(?:(?<=[^\W_])['’.](?=[^\W_])|(?<=[^\W\d_]):(?=[^\W\d_])|(?<=\d)[,;](?=\d))\w+)*"
)

_CLOSERS = re.escape("\"')]’”")
# The end of a statement before a match, through the whitespace that follows it.
_STATEMENT_BREAK = re.compile(rf"[.!?][{_CLOSERS}]*\s+|\n[ \t]*\n\s*")
# The end of a statement after a match: its punctuation, or the start of a blank line.
_STATEMENT_END = re.compile(rf"(?P<stop>[.!?][{_CLOSERS}]*)(?=\s|\Z)|\n[ \t]*\n")
_WHITESPACE = re.compile(r"\s+")


def tokens(text: str) -> list[str]:
    """The words of `text`, lower-cased, as the search index splits them."""
    return [match.group().lower() for match in _TOKEN.finditer(text)]


def find_phrase(text: str, phrase: Sequence[str]) -> tuple[int, int] | None:
    """Where the words of `phrase` first occur in `text` one after another, as (start, end)
    offsets into `text`, or None. Whatever separates the words in `text` is ignored, as
    pg_search ignores it."""
    if not phrase:
        return None
    words = list(_TOKEN.finditer(text))
    size = len(phrase)
    for i in range(len(words) - size + 1):
        if all(words[i + j].group().lower() == phrase[j] for j in range(size)):
            return words[i].start(), words[i + size - 1].end()
    return None


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
        from `offset`, each cited with a snippet around the phrase."""
        phrase = tokens(query)
        total = 0
        matches: list[PassageMatch] = []
        # A query with no words (only punctuation) can match nothing.
        if phrase:
            total = await self._repository.count_passages(query)
            if offset < total:
                matches = await self._repository.find_passages(query, limit, offset)
        return SearchResponse(
            query=query,
            total=total,
            limit=limit,
            offset=offset,
            results=[self._hit(match, phrase) for match in matches],
        )

    def _hit(self, match: PassageMatch, phrase: Sequence[str]) -> SearchHit:
        found = find_phrase(match.search_text, phrase)
        if found is None:
            # The index matched the passage, but our copy of its tokenizer splits the text
            # differently. Cite the start of the passage rather than drop the result.
            logger.warning(
                "search match not located in passage",
                extra={"identifier": match.identifier, "leaf": match.leaf},
            )
            found = (0, 0)
        text, match_start, match_end = snippet(match.search_text, *found)
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
            snippet=text,
            match_start=match_start,
            match_end=match_end,
            score=round(match.score, 4),
        )
