"""Run the Alembic migrations bundled with corpus_core, without an alembic.ini on disk."""

from pathlib import Path

from alembic import command
from alembic.config import Config


def alembic_config(database_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)
