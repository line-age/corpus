"""Search endpoints, mounted under /search."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from corpus.api.modules.search.repository import SearchRepository
from corpus.api.modules.search.service import SearchService
from corpus.db import get_session

router = APIRouter(prefix="/search", tags=["search"])


def get_search_service(session: Annotated[AsyncSession, Depends(get_session)]) -> SearchService:
    return SearchService(SearchRepository(session))

