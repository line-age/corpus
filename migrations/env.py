"""Alembic environment. The database URL never comes from alembic.ini.

A caller running Alembic from Python can pass a URL in the Config's attributes:

    config = Config("alembic.ini")
    config.attributes["url"] = get_settings().database_url.set(database="other")
    command.upgrade(config, "head")

Otherwise, including from the alembic CLI, it comes from corpus settings.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from corpus.config import get_settings
from corpus.db import Base

config = context.config

if config.config_file_name is not None:
    # Keep loggers configured before Alembic runs (e.g. under pytest) working.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata
url = config.attributes.get("url") or get_settings().database_url


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
