"""Search endpoints, mounted under /api/v1/search."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from corpus.api.modules.search.dto import SearchParams, SearchResponse
from corpus.api.modules.search.repository import SearchRepository
from corpus.api.modules.search.service import SearchService
from corpus.config import get_settings
from corpus.db import get_session

router = APIRouter(prefix="/search", tags=["search"])


def get_search_service(session: Annotated[AsyncSession, Depends(get_session)]) -> SearchService:
    return SearchService(
        SearchRepository(session), archive_org_base_url=get_settings().archive_org_base_url
    )


@router.get("")
async def search(
    params: Annotated[SearchParams, Query()],
    service: Annotated[SearchService, Depends(get_search_service)],
) -> SearchResponse:
    """Find the passages of OCR text that contain a word or phrase, each with a citation:
    the publication, the page and a link to it, and the statement that contains it.

    Results are ordered by relevance (BM25), highest `score` first."""
    return await service.search(params.q, limit=params.limit, offset=params.offset)
