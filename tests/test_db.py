from typing import Annotated

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from corpus.api.main import create_app
from corpus.db import get_session


def test_get_session_injects_a_session_on_the_configured_database(migrated_db):
    app = create_app()

    @app.get("/_test/current-database")
    async def current_database(session: Annotated[AsyncSession, Depends(get_session)]) -> str:
        return await session.scalar(text("SELECT current_database()"))

    with TestClient(app) as client:
        response = client.get("/_test/current-database")

    assert response.status_code == 200
    assert response.json() == migrated_db.postgres_db
