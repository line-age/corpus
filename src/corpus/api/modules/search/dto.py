"""Request and response models for the search API."""

from datetime import date
from typing import Annotated

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
    """One passage containing the query, with the statement in it that contains the query."""

    document: DocumentCitation
    page: PageCitation
    snippet: str
    """The statement containing the first match in the passage, whitespace collapsed;
    "..." marks a cut."""
    match_start: int
    """Offset in `snippet` where the matched text starts."""
    match_end: int
    """Offset in `snippet` just past the matched text."""
    score: float
    """Relevance of the passage to the query (BM25); higher is more relevant. Results are
    ordered by it, highest first."""


class SearchResponse(BaseModel):
    query: str
    total: int
    """Every passage containing the query, not only those in `results`."""
    limit: int
    offset: int
    results: list[SearchHit]
