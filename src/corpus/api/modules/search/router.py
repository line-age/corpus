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
        SearchRepository(session), ranking=get_settings().search_ranking_enabled
    )


@router.get("")
async def search(
    params: Annotated[SearchParams, Query()],
    service: Annotated[SearchService, Depends(get_search_service)],
) -> SearchResponse:
    """Find every occurrence of a word or phrase in the OCR text, each with a citation:
    the publication, the page and a link to it, and the statement that contains it.

    Results are in corpus order (publication date, then page), or by relevance when the
    server has ranking turned on; `order` in the response says which."""
    return await service.search(params.q, limit=params.limit, offset=params.offset)
