"""IngestService and IngestRepository against the migrated test database, with a mocked
archive.org client.

The test database is shared with other test files, so every test uses its own
identifiers and deletes its rows afterwards, and nothing here assumes the tables are
otherwise empty.
"""

import asyncio
from collections.abc import Iterator
from unittest.mock import create_autospec
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, text

from corpus.config import Settings
from corpus.db import make_engine, make_sessionmaker
from corpus.ingestion.client import ArchiveOrgClient, SourceItem
from corpus.ingestion.exceptions import FetchError, InvalidSourceData
from corpus.ingestion.repository import IngestRepository
from corpus.ingestion.service import IngestResult, IngestService


def make_source(
    identifier: str, pages: list[tuple[str, str]], **metadata: str
) -> SourceItem:
    """An item whose pages are (text, printed page number) pairs."""
    text, index, numbers = "", [], []
    for leaf, (page_text, number) in enumerate(pages):
        index.append([len(text), len(text) + len(page_text), 0, 0])
        text += page_text
        numbers.append({"leafNum": leaf, "pageNumber": number})
    return SourceItem(
        identifier=identifier,
        source_url=f"https://archive.example/details/{identifier}",
        metadata={"title": "The Journal", "date": "1951-06-07"} | metadata,
        text=text,
        page_index=index,
        page_numbers={"pages": numbers},
    )


@pytest.fixture
def db(migrated_db: Settings) -> Iterator[Engine]:
    engine = create_engine(migrated_db.database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def identifier(db: Engine) -> Iterator[str]:
    """A fresh identifier; its document and pages are deleted after the test."""
    value = f"test-ingest-{uuid4().hex}"
    yield value
    with db.begin() as conn:
        conn.execute(text("DELETE FROM documents WHERE identifier = :id"), {"id": value})


@pytest.fixture
def client():
    return create_autospec(ArchiveOrgClient, instance=True)


def ingest(settings: Settings, client, identifier: str) -> IngestResult:
    async def run() -> IngestResult:
        engine = make_engine(settings)
        try:
            return await IngestService(client, make_sessionmaker(engine)).ingest(identifier)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def stored(db: Engine, identifier: str) -> tuple[dict | None, list[tuple]]:
    """The document row (as a dict) and its pages as (leaf, page number, text)."""
    with db.connect() as conn:
        document = conn.execute(
            text("SELECT * FROM documents WHERE identifier = :id"), {"id": identifier}
        ).mappings().one_or_none()
        pages = conn.execute(
            text(
                "SELECT leaf_number, page_number, text FROM pages"
                " JOIN documents ON documents.id = pages.document_id"
                " WHERE identifier = :id ORDER BY leaf_number"
            ),
            {"id": identifier},
        ).all()
    return (dict(document) if document else None), [tuple(page) for page in pages]


def test_ingest_stores_the_document_and_one_row_per_page(migrated_db, db, client, identifier):
    client.fetch.return_value = make_source(
        identifier, [("Cover", ""), ("Page one", "1"), ("Page two", "2")], volume="50", issue="5"
    )

    result = ingest(migrated_db, client, identifier)

    client.fetch.assert_called_once_with(identifier)
    document, pages = stored(db, identifier)
    assert result == IngestResult(identifier, document["id"], pages=3, numbered_pages=2)
    assert document["title"] == "The Journal"
    assert str(document["publication_date"]) == "1951-06-07"
    assert (document["volume"], document["issue"]) == ("50", "5")
    assert document["source_url"] == f"https://archive.example/details/{identifier}"
    assert document["ingested_at"] is not None
    assert pages == [(0, None, "Cover"), (1, "1", "Page one"), (2, "2", "Page two")]


def test_reingesting_replaces_metadata_and_pages_in_place(migrated_db, db, client, identifier):
    client.fetch.return_value = make_source(
        identifier, [("Old cover", ""), ("Old one", "1"), ("Old two", "2")], volume="50"
    )
    first = ingest(migrated_db, client, identifier)
    first_document, _ = stored(db, identifier)

    client.fetch.return_value = make_source(
        identifier, [("New cover", ""), ("New one", "1")], title="New title", date="1951"
    )
    second = ingest(migrated_db, client, identifier)

    document, pages = stored(db, identifier)
    assert second.document_id == first.document_id == document["id"]
    assert document["title"] == "New title"
    assert document["publication_date"] is None
    assert document["volume"] is None
    assert document["ingested_at"] >= first_document["ingested_at"]
    assert pages == [(0, None, "New cover"), (1, "1", "New one")]


def test_reingesting_the_same_item_is_idempotent(migrated_db, db, client, identifier):
    client.fetch.return_value = make_source(identifier, [("Cover", ""), ("Page one", "1")])

    ingest(migrated_db, client, identifier)
    first = stored(db, identifier)
    ingest(migrated_db, client, identifier)
    second = stored(db, identifier)

    assert {k: v for k, v in first[0].items() if k != "ingested_at"} == {
        k: v for k, v in second[0].items() if k != "ingested_at"
    }
    assert first[1] == second[1]
    with db.connect() as conn:
        count = conn.scalar(
            text("SELECT count(*) FROM documents WHERE identifier = :id"), {"id": identifier}
        )
    assert count == 1


def test_items_are_stored_separately(migrated_db, db, client, identifier):
    other = f"{identifier}-other"
    try:
        client.fetch.side_effect = lambda id_: make_source(id_, [(f"Text of {id_}", "")])
        ingest(migrated_db, client, identifier)
        ingest(migrated_db, client, other)

        assert stored(db, identifier)[1] == [(0, None, f"Text of {identifier}")]
        assert stored(db, other)[1] == [(0, None, f"Text of {other}")]
    finally:
        with db.begin() as conn:
            conn.execute(text("DELETE FROM documents WHERE identifier = :id"), {"id": other})


def test_a_failed_download_writes_nothing(migrated_db, db, client, identifier):
    client.fetch.side_effect = FetchError("GET ... failed: HTTP 503")

    with pytest.raises(FetchError):
        ingest(migrated_db, client, identifier)

    assert stored(db, identifier) == (None, [])


def test_invalid_data_leaves_the_earlier_copy_untouched(migrated_db, db, client, identifier):
    client.fetch.return_value = make_source(identifier, [("Cover", ""), ("Page one", "1")])
    ingest(migrated_db, client, identifier)
    before = stored(db, identifier)

    broken = make_source(identifier, [("New cover", "")], title="New title")
    client.fetch.return_value = SourceItem(**(vars(broken) | {"page_index": [[0, 999]]}))
    with pytest.raises(InvalidSourceData):
        ingest(migrated_db, client, identifier)

    assert stored(db, identifier) == before


def test_a_database_failure_rolls_back_the_whole_item(
    migrated_db, db, client, identifier, monkeypatch
):
    client.fetch.return_value = make_source(identifier, [("Cover", ""), ("Page one", "1")])
    ingest(migrated_db, client, identifier)
    before = stored(db, identifier)

    # Fail after the document has been updated and its pages replaced.
    replace_document = IngestRepository.replace_document

    async def replace_then_fail(self, item):
        await replace_document(self, item)
        raise RuntimeError("connection lost")

    monkeypatch.setattr(IngestRepository, "replace_document", replace_then_fail)
    client.fetch.return_value = make_source(identifier, [("New cover", "")], title="New title")
    with pytest.raises(RuntimeError):
        ingest(migrated_db, client, identifier)

    assert stored(db, identifier) == before


def test_already_ingested_names_only_the_stored_items(migrated_db, client, identifier):
    client.fetch.return_value = make_source(identifier, [("Page", "1")])
    ingest(migrated_db, client, identifier)
    missing = f"test-ingest-{uuid4().hex}"

    async def run() -> set[str]:
        engine = make_engine(migrated_db)
        try:
            service = IngestService(client, make_sessionmaker(engine))
            assert await service.already_ingested([]) == set()
            return await service.already_ingested([identifier, missing])
        finally:
            await engine.dispose()

    assert asyncio.run(run()) == {identifier}
