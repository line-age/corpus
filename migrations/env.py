"""Alembic environment. The database URL never comes from alembic.ini.

Autogenerate compares against the ingest schema in corpus_core.db, the only tables.

A caller running Alembic from Python can pass a URL in the Config's attributes:

    config = Config("alembic.ini")
    config.attributes["url"] = get_settings().database_url.set(database="other")
    command.upgrade(config, "head")

From the alembic CLI, `-x url=...` does the same:

    alembic -x url=postgresql+psycopg://corpus:corpus@localhost:5433/corpus upgrade head

Otherwise it comes from corpus settings.
"""

from logging.config import fileConfig

from alembic import context
from corpus_core.db import metadata as target_metadata
from sqlalchemy import create_engine, pool

from corpus.config import get_settings

config = context.config

if config.config_file_name is not None:
    # Keep loggers configured before Alembic runs (e.g. under pytest) working.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

url = (
    config.attributes.get("url")
    or context.get_x_argument(as_dictionary=True).get("url")
    or get_settings().database_url
)


def run_migrations_offline() -> None:
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
