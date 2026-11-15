from alembic import context
from corpus_core.config import get_settings
from corpus_core.db import metadata
from sqlalchemy import engine_from_config, pool

config = context.config
config.set_main_option(
    "sqlalchemy.url", config.get_main_option("sqlalchemy.url") or get_settings().database_url
)


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
