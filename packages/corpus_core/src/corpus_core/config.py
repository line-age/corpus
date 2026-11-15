"""Runtime settings. Values come from the environment (prefix CORPUS_), never from the repo."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CORPUS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://corpus:corpus@localhost:5432/corpus"
    # file:///path for a local lake, s3://bucket[/prefix] for object storage
    raw_store: str = "file:///tmp/corpus-raw"
    s3_endpoint_url: str | None = None
    ia_base_url: str = "https://archive.org"
    user_agent: str = "lineage-corpus/0.1 (+https://github.com/line-age/corpus)"
    concurrency: int = 8
    http_timeout_s: float = 60.0
    max_attempts: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
