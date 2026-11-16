"""Request and response models for the search API."""

from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, StringConstraints


def _no_nul(value: str) -> str:
    # Postgres text cannot hold NUL, so a query containing one could never match.
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


SearchText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=200),
    AfterValidator(_no_nul),
]


class SearchParams(BaseModel):
    """Query parameters of GET /api/v1/search."""

    q: SearchText = Field(description="Words or phrase to find, such as a place name.")
    limit: int = Field(default=20, ge=1, le=100, description="Most results to return.")
    offset: int = Field(default=0, ge=0, description="Results to skip, for paging.")


class DocumentCitation(BaseModel):
    """The publication a result comes from."""

    identifier: str
    title: str
    publication_date: date | None
    volume: str | None
    issue: str | None
    source_url: str


class PageCitation(BaseModel):
    """The scanned page a result comes from."""

    leaf_number: int
    """0-based position of the page in the scan."""
    page_number: str | None
    """The page number printed on the page, when known."""
    url: str
    """Link to this page of the scan."""


class SearchHit(BaseModel):
    """One occurrence of the query, with the statement that contains it."""

    document: DocumentCitation
    page: PageCitation
    snippet: str
    """The sentence containing the match, whitespace collapsed; "..." marks a cut."""
    match_start: int
    """Offset in `snippet` where the matched text starts."""
    match_end: int
    """Offset in `snippet` just past the matched text."""
    score: float | None
    """Relevance of the result's page to the query (BM25); higher is more relevant.
    Null when results are in corpus order."""


Order = Literal["corpus", "relevance"]


class SearchResponse(BaseModel):
    query: str
    order: Order
    """How `results` are ordered: "corpus" is by publication date, document, page and
    position on the page; "relevance" is by score, highest first, then corpus order."""
    total: int
    """Every occurrence of the query, not only those in `results`."""
    limit: int
    offset: int
    results: list[SearchHit]
