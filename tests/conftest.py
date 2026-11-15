import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def hocr_bytes() -> bytes:
    return (FIXTURES / "sample_hocr.html").read_bytes()


@pytest.fixture
def page_numbers_bytes() -> bytes:
    return (FIXTURES / "sample_page_numbers.json").read_bytes()


@pytest.fixture
def database_url() -> str:
    url = os.environ.get("CORPUS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set CORPUS_TEST_DATABASE_URL to run database tests")
    return url
