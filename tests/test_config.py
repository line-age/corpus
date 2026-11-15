import pytest
from pydantic import ValidationError

from corpus.config import Settings
from corpus.db import make_engine


def test_engine_pool_follows_settings():
    engine = make_engine(Settings(postgres_pool_size=3, postgres_max_connections=8))

    assert engine.pool.size() == 3
    assert engine.pool._max_overflow == 5  # no public accessor; max connections minus pool


def test_max_connections_equal_to_pool_size_means_no_overflow():
    engine = make_engine(Settings(postgres_pool_size=4, postgres_max_connections=4))

    assert engine.pool._max_overflow == 0


def test_max_connections_below_pool_size_is_rejected():
    with pytest.raises(ValidationError, match="POSTGRES_MAX_CONNECTIONS"):
        Settings(postgres_pool_size=10, postgres_max_connections=5)


def test_log_level_is_case_insensitive(configure):
    assert configure(log_level="debug").log_level == "DEBUG"


def test_unknown_log_level_is_rejected():
    with pytest.raises(ValidationError):
        Settings(log_level="loud")
