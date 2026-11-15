"""Shared fixtures.

The app reads settings only from get_settings(), so tests change them the way a deployment
does: through environment variables. `configure(docs_enabled=True)` sets DOCS_ENABLED for
one test and clears the settings cache before and after, so nothing leaks between tests.

Tests run inside the API container (`make test`) against the compose database, but never
touch the development database: the fixtures below create their own, named after
POSTGRES_DB, and recreate them at the start of every session.
"""

from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from corpus.api.main import create_app
from corpus.config import Settings, get_settings

ROOT = Path(__file__).resolve().parents[1]


def _env_value(value: Any) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def _recreate_database(name: str) -> None:
    admin_url = get_settings().database_url.set(database="postgres")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            quoted = conn.dialect.identifier_preparer.quote(name)
            conn.execute(text(f"DROP DATABASE IF EXISTS {quoted} WITH (FORCE)"))
            conn.execute(text(f"CREATE DATABASE {quoted}"))
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def _migrated_db_name() -> str:
    settings = get_settings()
    name = f"{settings.postgres_db}_test"
    _recreate_database(name)
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["url"] = settings.database_url.set(database=name)
    command.upgrade(config, "head")
    return name


@pytest.fixture(scope="session")
def _bare_db_name() -> str:
    name = f"{get_settings().postgres_db}_test_bare"
    _recreate_database(name)
    return name


@pytest.fixture
def configure(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., Settings]]:
    """Override settings for one test by field name, returning the resulting settings."""
    get_settings.cache_clear()

    def apply(**overrides: Any) -> Settings:
        for field, value in overrides.items():
            if field not in Settings.model_fields:
                raise ValueError(f"no such setting: {field}")
            monkeypatch.setenv(field.upper(), _env_value(value))
        get_settings.cache_clear()
        return get_settings()

    yield apply
    get_settings.cache_clear()


@pytest.fixture
def migrated_db(configure, _migrated_db_name) -> Settings:
    """Point settings at a fresh test database with every migration applied."""
    return configure(postgres_db=_migrated_db_name)


@pytest.fixture
def bare_db(configure, _bare_db_name) -> Settings:
    """Point settings at a fresh test database with no migrations applied."""
    return configure(postgres_db=_bare_db_name)


@pytest.fixture
def make_client() -> Iterator[Callable[[], TestClient]]:
    """Start an app built from the current settings. Call configure() first to change them."""
    with ExitStack() as stack:

        def make() -> TestClient:
            return stack.enter_context(TestClient(create_app()))

        yield make
