"""Runtime configuration.

Every setting is read from the environment, falling back to a `.env` file in the working
directory, then to the defaults below. Field names map to upper-case variables:
`postgres_host` is `POSTGRES_HOST`. Get the shared instance with:

    from corpus.config import get_settings

    settings = get_settings()
"""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # API
    docs_enabled: bool = False
    """Serve interactive docs at /docs and /redoc, and the schema at /openapi.json."""

    healthz_timeout_seconds: float = Field(default=3.0, gt=0)
    """How long each /healthz check may take before it is reported as failed."""

    # Logging
    log_level: LogLevel = "INFO"
    """Lowest level the corpus logger emits. Case-insensitive in the environment."""

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_case_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    # Postgres. The names match the official postgres image's variables, so one .env
    # configures both the database container and the API's connection to it.
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "corpus"
    postgres_password: SecretStr = SecretStr("corpus")
    postgres_db: str = "corpus"
    postgres_connect_timeout: int = Field(default=5, ge=1)
    """Seconds to wait for a new database connection."""

    # Connection pool, per process: total connections to Postgres are at most
    # postgres_max_connections times the number of API processes (workers x replicas).
    postgres_pool_size: int = Field(default=5, ge=1)
    """Connections kept open in the pool."""
    postgres_max_connections: int = Field(default=10, ge=1)
    """Most connections open at once: the pool plus short-lived overflow under load."""

    @model_validator(mode="after")
    def _max_connections_cover_pool(self) -> Self:
        if self.postgres_max_connections < self.postgres_pool_size:
            raise ValueError("POSTGRES_MAX_CONNECTIONS must be at least POSTGRES_POOL_SIZE")
        return self

    @property
    def database_url(self) -> URL:
        """SQLAlchemy URL for the configured database. Its repr masks the password."""
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    """Settings for this process, read from the environment on first call and then cached."""
    return Settings()
