"""GET /api/v1/search and SearchRepository against the migrated test database.

The test database is shared by the whole session, so the fixture's text uses made-up words
("Zorbatown") that no other rows contain, and tests never assume the tables are otherwise
empty.
"""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from corpus.api.modules.search.repository import SearchRepository
from corpus.config import Settings
from corpus.db import make_engine, make_sessionmaker
from corpus.models import Document, Page

URL = "/api/v1/search"


@pytest.fixture
def corpus(migrated_db: Settings) -> Iterator[dict[str, Document]]:
    """Two documents with Zorbatown pages, deleted again after the test."""
    suffix = uuid.uuid4().hex
    late = Document(
        identifier=f"test-search-late-{suffix}",
        title="Zorbatown Gazette",
        publication_date=date(1951, 6, 7),
        volume="50",
        issue="5",
        source_url=f"https://archive.org/details/test-search-late-{suffix}",
        pages=[
            Page(
                leaf_number=7,
                page_number="5",
                text="Humble has staked a northwest Zorbatown\nCounty wildcat. "
                "A second Zorbatown County test is planned.",
            ),
            Page(leaf_number=2, text="zorbatown county news. Zorbatownville is elsewhere."),
            Page(leaf_number=9, text="Zorb%town and Zorb_town and Zorb\\town are literal."),
            Page(leaf_number=10, text="Zorbxtown, ZorbXYtown and Zorbtown are other words."),
        ],
    )
    early = Document(
        identifier=f"test-search-early-{suffix}",
        title="Zorbatown Gazette",
        publication_date=date(1950, 1, 5),
        source_url=f"https://archive.org/details/test-search-early-{suffix}",
        pages=[Page(leaf_number=4, page_number="2", text="Early Zorbatown County report.")],
    )
    engine = create_engine(migrated_db.database_url)
    try:
        with Session(engine, expire_on_commit=False) as session:
            session.add_all([late, early])
            session.commit()
        yield {"late": late, "early": early}
        with Session(engine) as session:
            session.execute(delete(Document).where(Document.id.in_([late.id, early.id])))
            session.commit()
    finally:
        engine.dispose()


def test_returns_every_occurrence_with_citations_in_corpus_order(corpus, make_client):
    response = make_client().get(URL, params={"q": "Zorbatown County"})

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "Zorbatown County"
    assert body["total"] == 4
    assert (body["limit"], body["offset"]) == (20, 0)
    assert [(hit["document"]["identifier"], hit["page"]["leaf_number"]) for hit in body["results"]] == [
        (corpus["early"].identifier, 4),
        (corpus["late"].identifier, 2),
        (corpus["late"].identifier, 7),
        (corpus["late"].identifier, 7),
    ]

    hit = body["results"][2]
    assert hit["document"] == {
        "identifier": corpus["late"].identifier,
        "title": "Zorbatown Gazette",
        "publication_date": "1951-06-07",
        "volume": "50",
        "issue": "5",
        "source_url": corpus["late"].source_url,
    }
    assert hit["page"] == {
        "leaf_number": 7,
        "page_number": "5",
        "url": f"{corpus['late'].source_url}/page/n7",
    }
    assert hit["snippet"] == "Humble has staked a northwest Zorbatown County wildcat."
    assert hit["snippet"][hit["match_start"] : hit["match_end"]] == "Zorbatown County"
    assert body["results"][3]["snippet"] == "A second Zorbatown County test is planned."
    assert body["results"][1]["page"]["page_number"] is None


def test_query_is_stripped_and_paged(corpus, make_client):
    response = make_client().get(
        URL, params={"q": "  Zorbatown\tCounty  ", "limit": 2, "offset": 1}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "Zorbatown\tCounty"
    assert body["total"] == 4
    assert [hit["page"]["leaf_number"] for hit in body["results"]] == [2, 7]


def test_matches_whole_words_only(corpus, make_client):
    body = make_client().get(URL, params={"q": "Zorbatown"}).json()

    # Four "Zorbatown"s across the pages; "Zorbatownville" is not one.
    assert body["total"] == 4
    assert "Zorbatownville is elsewhere." not in [hit["snippet"] for hit in body["results"]]


def test_no_matches(corpus, make_client):
    body = make_client().get(URL, params={"q": "Zorbatown Parish"}).json()

    assert body["total"] == 0
    assert body["results"] == []


def test_like_wildcards_in_the_query_are_literal(corpus, make_client):
    body = make_client().get(URL, params={"q": "Zorb_town"}).json()

    assert body["total"] == 1
    hit = body["results"][0]
    assert hit["snippet"][hit["match_start"] : hit["match_end"]] == "Zorb_town"


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        # Unescaped, % and _ would also match "Zorbxtown" and "ZorbXYtown" on leaf 10.
        ("Zorb%town", [9]),
        ("Zorb_town", [9]),
        # Unescaped, the backslash would escape the "t" and match "Zorbtown" on leaf 10.
        ("Zorb\\town", [9]),
        ("zorbXtown", [10]),
    ],
)
def test_repository_escapes_like_wildcards(migrated_db, corpus, token, expected):
    async def find() -> list[Page]:
        engine = make_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                return await SearchRepository(session).find_pages_containing([token])
        finally:
            await engine.dispose()

    ours = {corpus["late"].id, corpus["early"].id}
    pages = [page for page in asyncio.run(find()) if page.document_id in ours]

    assert [page.leaf_number for page in pages] == expected


def test_repository_requires_every_token_and_orders_by_date_then_leaf(migrated_db, corpus):
    async def find() -> list[tuple[str, int]]:
        engine = make_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                pages = await SearchRepository(session).find_pages_containing(
                    ["county", "ZORBATOWN"]
                )
                return [(page.document.identifier, page.leaf_number) for page in pages]
        finally:
            await engine.dispose()

    ours = {corpus["late"].identifier, corpus["early"].identifier}
    found = [row for row in asyncio.run(find()) if row[0] in ours]

    assert found == [
        (corpus["early"].identifier, 4),
        (corpus["late"].identifier, 2),
        (corpus["late"].identifier, 7),
    ]


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": ""},
        {"q": "a"},
        {"q": "   a   "},
        {"q": "x" * 201},
        {"q": "ab\x00"},
        {"q": "Zorbatown", "limit": 0},
        {"q": "Zorbatown", "limit": 101},
        {"q": "Zorbatown", "limit": "many"},
        {"q": "Zorbatown", "offset": -1},
    ],
)
def test_rejects_invalid_parameters(migrated_db, make_client, params):
    response = make_client().get(URL, params=params)

    assert response.status_code == 422


def test_accepts_a_query_of_200_characters_after_stripping(migrated_db, make_client):
    response = make_client().get(URL, params={"q": "  " + "x" * 200 + "  "})

    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_results_are_in_corpus_order_without_scores_by_default(corpus, make_client):
    body = make_client().get(URL, params={"q": "Zorbatown County"}).json()

    assert body["order"] == "corpus"
    assert {hit["score"] for hit in body["results"]} == {None}


def test_ranking_flag_orders_results_by_score(corpus, configure, make_client):
    corpus_order = make_client().get(URL, params={"q": "Zorbatown County"}).json()
    configure(search_ranking_enabled=True)

    body = make_client().get(URL, params={"q": "Zorbatown County"}).json()

    assert body["order"] == "relevance"
    assert body["total"] == corpus_order["total"] == 4
    scores = [hit["score"] for hit in body["results"]]
    assert all(score > 0 for score in scores)
    assert scores == sorted(scores, reverse=True)

    def cited(hit):
        return (hit["document"]["identifier"], hit["page"]["leaf_number"], hit["snippet"])

    # The same occurrences, reordered; both on leaf 7 share its score.
    assert sorted(map(cited, body["results"])) == sorted(map(cited, corpus_order["results"]))
    leaf_7 = {hit["score"] for hit in body["results"] if hit["page"]["leaf_number"] == 7}
    assert len(leaf_7) == 1


def test_corpus_stats_count_every_page(corpus, migrated_db):
    async def stats():
        engine = make_engine(migrated_db)
        try:
            async with make_sessionmaker(engine)() as session:
                return await SearchRepository(session).get_corpus_stats()
        finally:
            await engine.dispose()

    result = asyncio.run(stats())

    # Other test files may add pages to the shared database, so only a lower bound.
    assert result.pages >= 5
    assert result.average_page_length > 0
