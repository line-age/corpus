from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from corpus import __version__
from corpus.api.middlewares.request_logger import RequestLoggerMiddleware
from corpus.api.modules.health.router import router as health_router
from corpus.api.modules.search.router import router as search_router
from corpus.config import get_settings
from corpus.db import make_engine, make_sessionmaker
from corpus.lib.logger import configure_logging

DESCRIPTION = "Lineage trade-press ingest and authority-ranked RAG search."


class IndexResponse(BaseModel):
    name: str
    description: str
    version: str
    docs: str | None
    """Path to the interactive docs, or null when they are disabled."""


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = make_engine(settings)
        app.state.sessionmaker = make_sessionmaker(app.state.engine)
        try:
            yield
        finally:
            await app.state.engine.dispose()

    docs = settings.docs_enabled
    app = FastAPI(
        title="corpus",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.add_middleware(RequestLoggerMiddleware)

    @app.get("/", tags=["meta"])
    async def index() -> IndexResponse:
        return IndexResponse(
            name="corpus", description=DESCRIPTION, version=__version__, docs=app.docs_url
        )

    app.include_router(health_router)
    app.include_router(search_router)
    return app
