"""GET /api/v1/search and SearchRepository against the migrated test database.

The test database is shared by the whole session, so the fixture's text uses made-up words
("Zorbatown") that no other rows contain, and tests never assume the tables are otherwise
empty. BM25 scores depend on every row in the index, so ranking tests compare only
passages whose order doesn't depend on the rest: same length with more occurrences, or
the same occurrences in a longer passage.
"""

import asyncio
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

import pytest
from corpus_core import db
from sqlalchemy import create_engine, delete, insert

from corpus.api.modules.search.repository import PassageMatch, SearchRepository
from corpus.config import Settings
from corpus.db import make_engine, make_sessionmaker

URL = "/api/v1/search"

# Three occurrences, and one, in passages of the same length.
MANY = "Zorbatown County wildcat. Zorbatown County test. Zorbatown County well."
ONE = "Zorbatown County wildcat. Another field test. Another oil well."
# One occurrence in a much longer passage.
LONG = "Zorbatown County report. " + "Filler words about other things. " * 60
EARLY = "Early Zorbatown County report."
QUORVALE = "Basin news \u2014 \u00dcber caf\u00e9. The Quorvale Basin lies here."


@dataclass(frozen=True)
class Corpus:
    late: str
    """Identifier of the 1951 item."""
    early: str
    """Identifier of the 1950 item."""


@pytest.fixture
def corpus(migrated_db: Settings) -> Iterator[Corpus]:
    """Two items with Zorbatown passages, deleted again after the test."""
    suffix = uuid.uuid4().hex
    collection = f"test-search-{suffix}"
    late, early = f"test-search-late-{suffix}", f"test-search-early-{suffix}"
    # (identifier, leaf, printed page, passage texts)
    pages = [
        (late, 7, "5", [MANY]),
        (late, 8, None, [ONE]),
        (late, 9, None, [LONG]),
        (
            late,
            2,
            None,
            ["Zorbatownville is elsewhere. Zorb_town and Zorb%town are odd."],
        ),
        (late, 3, None, ["County of Zorbatown."]),
        # Multi-byte characters before the phrase, and one of its words alone.
        (late, 11, None, [QUORVALE]),
        (early, 4, "2", [EARLY]),
    ]
    engine = create_engine(migrated_db.database_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(db.publication).values(
                    collection=collection, name="Zorbatown Gazette", authority_tier=1
                )
            )
            conn.execute(
                insert(db.item),
                [
                    {
                        "identifier": late,
                        "collection": collection,
                        "title": "Zorbatown Gazette 1951-06-07",
                        "issue_date": date(1951, 6, 7),
                        "volume": "50",
                        "issue": "5",
                    },
                    {
                        "identifier": early,
                        "collection": collection,
                        "title": "Zorbatown Gazette 1950-01-05",
                        "issue_date": date(1950, 1, 5),
                        "volume": None,
                        "issue": None,
                    },
                ],
            )
            for identifier, leaf, printed_page, texts in pages:
                raw = " ".join(texts)
                conn.execute(
                    insert(db.page).values(
                        identifier=identifier,
                        leaf=leaf,
                        printed_page=printed_page,
                        raw_text=raw,
                        search_text=raw,
                        word_count=len(raw.split()),
                        text_hash=uuid.uuid4().hex,
                        pipeline_version=1,
                    )
                )
                for seq, text in enumerate(texts):
                    conn.execute(
                        insert(db.passage).values(
                            identifier=identifier,
                            leaf=leaf,
                            seq=seq,
                            char_start=0,
                            char_end=len(text),
                            word_count=len(text.split()),
                            search_text=text,
                            collection=collection,
                            issue_date=date(1951, 6, 7)
                            if identifier == late
                            else date(1950, 1, 5),
                            kind="issue",
                            license_lane="public-domain",
                            pipeline_version=1,
                        )
                    )
        yield Corpus(late=late, early=early)
        with engine.begin() as conn:
            # Pages and passages go with their items.
            conn.execute(delete(db.item).where(db.item.c.identifier.in_([late, early])))
            conn.execute(
                delete(db.publication).where(db.publication.c.collection == collection)
            )
    finally:
        engine.dispose()


def leaves(body: dict, corpus: Corpus) -> list[tuple[str, int]]:
    """(item, leaf) of each result, with the item named "late" or "early"."""
    names = {corpus.late: "late", corpus.early: "early"}
    return [
        (names[hit["document"]["identifier"]], hit["page"]["leaf_number"])
        for hit in body["results"]
    ]


def matched(hit: dict) -> list[str]:
    """The matched text of each of a result's snippets."""
    return [s["text"][s["match_start"] : s["match_end"]] for s in hit["snippets"]]


def test_returns_matching_passages_ranked_by_score(corpus, make_client):
    response = make_client().get(URL, params={"q": "Zorbatown County"})

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "Zorbatown County"
    assert "order" not in body
    assert body["total"] == 4
    assert (body["limit"], body["offset"]) == (20, 0)
    found = leaves(body, corpus)
    assert sorted(found) == [("early", 4), ("late", 7), ("late", 8), ("late", 9)]
    scores = [hit["score"] for hit in body["results"]]
    assert all(score > 0 for score in scores)
    assert scores == sorted(scores, reverse=True)
    # More occurrences in the same length rank higher; the same in a longer passage, lower.
    assert (
        found.index(("late", 7)) < found.index(("late", 8)) < found.index(("late", 9))
    )
    assert all(set(matched(hit)) == {"Zorbatown County"} for hit in body["results"])


def test_each_result_is_cited(corpus, configure, make_client):
    configure(archive_org_base_url="https://archive.example")

    body = make_client().get(URL, params={"q": "Zorbatown County"}).json()

    [hit] = [h for h in body["results"] if h["page"]["leaf_number"] == 7]
    assert hit["document"] == {
        "identifier": corpus.late,
        "title": "Zorbatown Gazette 1951-06-07",
        "publication_date": "1951-06-07",
        "volume": "50",
        "issue": "5",
        "source_url": f"https://archive.example/details/{corpus.late}",
    }
    assert hit["page"] == {
        "leaf_number": 7,
        "page_number": "5",
        "url": f"https://archive.example/details/{corpus.late}/page/n7",
    }
    # A snippet for each of the passage's three occurrences, in order.
    assert [s["text"] for s in hit["snippets"]] == [
        "Zorbatown County wildcat.",
        "Zorbatown County test.",
        "Zorbatown County well.",
    ]
    [early] = [
        h for h in body["results"] if h["document"]["identifier"] == corpus.early
    ]
    assert early["document"]["volume"] is None
    assert early["page"]["page_number"] == "2"


def test_query_is_stripped_and_paged(corpus, make_client):
    client = make_client()
    everything = client.get(URL, params={"q": "Zorbatown County"}).json()

    response = client.get(
        URL, params={"q": "  Zorbatown\tCounty  ", "limit": 2, "offset": 1}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "Zorbatown\tCounty"
    assert (body["total"], body["limit"], body["offset"]) == (4, 2, 1)
    assert leaves(body, corpus) == leaves(everything, corpus)[1:3]


def test_matches_whole_words_only(corpus, make_client):
    body = make_client().get(URL, params={"q": "zorbatown"}).json()

    # "Zorbatownville" on leaf 2 is another word.
    assert sorted(leaves(body, corpus)) == [
        ("early", 4),
        ("late", 3),
        ("late", 7),
        ("late", 8),
        ("late", 9),
    ]
    assert all(set(matched(hit)) == {"Zorbatown"} for hit in body["results"])


def test_matches_the_words_in_order(corpus, make_client):
    body = make_client().get(URL, params={"q": "County Zorbatown"}).json()

    # Leaf 3 has "County of Zorbatown": both words, but not as the phrase.
    assert body["total"] == 0


def test_punctuation_between_words_is_ignored_as_the_index_ignores_it(
    corpus, make_client
):
    body = make_client().get(URL, params={"q": "Zorb town"}).json()

    assert leaves(body, corpus) == [("late", 2)]
    assert matched(body["results"][0]) == ["Zorb%town"]


def test_an_underscore_joins_a_word(corpus, make_client):
    body = make_client().get(URL, params={"q": "Zorb_town"}).json()

    assert leaves(body, corpus) == [("late", 2)]
    assert matched(body["results"][0]) == ["Zorb_town"]


def test_cites_the_phrase_after_multibyte_characters(corpus, make_client):
    body = make_client().get(URL, params={"q": "Quorvale Basin"}).json()

    assert leaves(body, corpus) == [("late", 11)]
    # The lone "Basin" at the start is not the phrase.
    assert body["results"][0]["snippets"] == [
        {"text": "The Quorvale Basin lies here.", "match_start": 4, "match_end": 18}
    ]


def test_no_matches(corpus, make_client):
    body = make_client().get(URL, params={"q": "Zorbatown Parish"}).json()

    assert body["total"] == 0
    assert body["results"] == []


@pytest.mark.parametrize(
    "q",
    [
        'Zorbatown "County',
        "a:b",
        "%%",
        "(Zorbatown",
        "Zorbatown~2",
        "Zorbatown*",
        "x OR y",
    ],
)
def test_query_syntax_characters_are_not_interpreted(corpus, make_client, q):
    response = make_client().get(URL, params={"q": q})

    assert response.status_code == 200


def test_quotes_in_the_query_are_ignored(corpus, make_client):
    body = make_client().get(URL, params={"q": '"Zorbatown County"'}).json()

    assert body["total"] == 4


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


def _repository_call(settings: Settings, call):
    async def run():
        engine = make_engine(settings)
        try:
            async with make_sessionmaker(engine)() as session:
                return await call(SearchRepository(session))
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_repository_counts_matching_passages(migrated_db, corpus):
    count = _repository_call(
        migrated_db, lambda repo: repo.count_passages("ZORBATOWN county")
    )

    assert count == 4


def test_repository_finds_passages_by_score_and_pages_them(migrated_db, corpus):
    every = _repository_call(
        migrated_db, lambda repo: repo.find_passages("Zorbatown County", 100, 0)
    )
    page = _repository_call(
        migrated_db, lambda repo: repo.find_passages("Zorbatown County", 2, 1)
    )

    assert all(isinstance(match, PassageMatch) for match in every)
    assert len(every) == 4
    assert [m.score for m in every] == sorted((m.score for m in every), reverse=True)
    assert page == every[1:3]
    [many] = [m for m in every if m.leaf == 7]
    assert (many.identifier, many.title, many.issue_date) == (
        corpus.late,
        "Zorbatown Gazette 1951-06-07",
        date(1951, 6, 7),
    )
    assert (many.volume, many.issue, many.printed_page) == ("50", "5", "5")
    assert many.search_text == MANY


def test_repository_splits_the_query_as_the_index_does(migrated_db):
    terms = _repository_call(
        migrated_db,
        lambda repo: repo.query_terms("Zorb_town O'Brien, Gas-Light 3.5 %%"),
    )

    assert terms == ["zorb_town", "o'brien", "gas", "light", "3.5"]


def test_repository_finds_no_terms_in_a_query_without_words(migrated_db):
    assert (
        _repository_call(migrated_db, lambda repo: repo.query_terms("%% -- ..")) == []
    )


def test_repository_reports_word_positions_as_character_offsets(migrated_db, corpus):
    [match] = _repository_call(
        migrated_db, lambda repo: repo.find_passages("Quorvale Basin", 10, 0)
    )

    # Every occurrence of each query word, in order, including the lone "Basin".
    assert [match.search_text[start:end] for start, end in match.word_spans] == [
        "Basin",
        "Quorvale",
        "Basin",
    ]
