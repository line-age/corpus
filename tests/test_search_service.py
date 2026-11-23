"""SearchService against a mocked repository: citations, snippets and paging.

Matching and ranking happen in pg_search; test_search.py covers them against the database.
"""

import asyncio
import logging
from datetime import date
from unittest.mock import create_autospec

import pytest

from corpus.api.modules.search.repository import PassageMatch, SearchRepository
from corpus.api.modules.search.service import (
    SNIPPET_CONTEXT,
    SearchService,
    find_phrase,
    snippet,
    tokens,
)

BASE_URL = "https://archive.example"


def make_match(
    text: str, leaf: int = 0, printed_page: str | None = None, score: float = 1.0
) -> PassageMatch:
    return PassageMatch(
        identifier="oilgasjournal-test",
        title="The Oil and Gas Journal",
        issue_date=date(1951, 6, 7),
        volume="50",
        issue="5",
        leaf=leaf,
        printed_page=printed_page,
        search_text=text,
        score=score,
    )


@pytest.fixture
def repository():
    """A SearchRepository mock that finds nothing. Autospec limits it to the real class's
    methods and signatures, so a renamed or removed method fails the test."""
    mock = create_autospec(SearchRepository, instance=True)
    mock.count_passages.return_value = 0
    mock.find_passages.return_value = []
    return mock


def found(repository, *matches: PassageMatch, total: int | None = None) -> None:
    repository.find_passages.return_value = list(matches)
    repository.count_passages.return_value = len(matches) if total is None else total


def search(repository, query: str, limit: int = 20, offset: int = 0):
    service = SearchService(repository, archive_org_base_url=BASE_URL)
    return asyncio.run(service.search(query, limit=limit, offset=offset))


def matched(hit) -> str:
    return hit.snippet[hit.match_start : hit.match_end]


def test_no_matches(repository):
    result = search(repository, "Martin County")

    assert result.total == 0
    assert result.results == []
    repository.count_passages.assert_awaited_once_with("Martin County")
    # Nothing to fetch when nothing matches.
    repository.find_passages.assert_not_awaited()


def test_passes_the_query_limit_and_offset_to_the_repository(repository):
    found(repository, make_match("Martin County well."), total=30)

    result = search(repository, "Martin County", limit=5, offset=10)

    repository.find_passages.assert_awaited_once_with("Martin County", 5, 10)
    assert (result.total, result.limit, result.offset) == (30, 5, 10)


def test_offset_past_the_end_returns_the_total_without_fetching(repository):
    repository.count_passages.return_value = 2

    result = search(repository, "Martin", offset=2)

    assert result.total == 2
    assert result.results == []
    repository.find_passages.assert_not_awaited()


@pytest.mark.parametrize("query", ["%%", "-- ..", "!?"])
def test_a_query_without_words_skips_the_database(repository, query):
    result = search(repository, query)

    assert (result.query, result.total, result.results) == (query, 0, [])
    repository.count_passages.assert_not_awaited()


def test_results_keep_the_repository_order_and_scores(repository):
    found(
        repository,
        make_match("Tulsa, Tulsa and Tulsa.", leaf=2, score=1.234567),
        make_match("Tulsa once.", leaf=1, score=0.5),
    )

    result = search(repository, "Tulsa")

    assert [hit.page.leaf_number for hit in result.results] == [2, 1]
    assert [hit.score for hit in result.results] == [1.2346, 0.5]


def test_citation_links_to_the_page(repository):
    found(repository, make_match("Martin County.", leaf=148, printed_page="145"))

    hit = search(repository, "Martin County").results[0]

    assert hit.document.identifier == "oilgasjournal-test"
    assert hit.document.title == "The Oil and Gas Journal"
    assert hit.document.publication_date == date(1951, 6, 7)
    assert (hit.document.volume, hit.document.issue) == ("50", "5")
    assert hit.document.source_url == f"{BASE_URL}/details/oilgasjournal-test"
    assert hit.page.leaf_number == 148
    assert hit.page.page_number == "145"
    assert hit.page.url == f"{BASE_URL}/details/oilgasjournal-test/page/n148"


def test_a_trailing_slash_on_the_base_url_is_ignored(repository):
    found(repository, make_match("Martin."))
    service = SearchService(repository, archive_org_base_url=f"{BASE_URL}/")

    hit = asyncio.run(service.search("Martin", limit=20, offset=0)).results[0]

    assert hit.document.source_url == f"{BASE_URL}/details/oilgasjournal-test"


def test_snippet_is_the_statement_containing_the_first_match(repository):
    found(
        repository,
        make_match("First sentence here! Then a second about Martin County, which ends? "
                   "Then Martin County again."),
    )

    hit = search(repository, "Martin County").results[0]

    assert hit.snippet == "Then a second about Martin County, which ends?"
    assert matched(hit) == "Martin County"


def test_matching_ignores_case_and_keeps_the_original_text(repository):
    found(repository, make_match("A MARTIN county well."))

    hit = search(repository, "martin COUNTY").results[0]

    assert matched(hit) == "MARTIN county"


def test_punctuation_between_the_words_is_ignored_as_the_index_ignores_it(repository):
    found(repository, make_match("Drilled in Martin, County Clare."))

    hit = search(repository, "Martin County").results[0]

    assert matched(hit) == "Martin, County"


def test_a_match_the_service_cannot_locate_cites_the_start_of_the_passage(repository, caplog):
    found(repository, make_match("Opening statement. Nothing like the query here."))

    with caplog.at_level(logging.WARNING, logger="corpus"):
        hit = search(repository, "Martin County").results[0]

    assert hit.snippet == "Opening statement."
    assert hit.match_start == hit.match_end == 0
    assert any(r.getMessage() == "search match not located in passage" for r in caplog.records)
    # The query is not logged.
    assert "Martin" not in caplog.text


# Tokenizing as the index does


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Martin County", ["martin", "county"]),
        ("Gas-Light co.", ["gas", "light", "co"]),
        ("O'Brien and O’Brien", ["o'brien", "and", "o’brien"]),
        ("3.5 ft, 1,000 bbl", ["3.5", "ft", "1,000", "bbl"]),
        ("Zorb_town Zorb%town", ["zorb_town", "zorb", "town"]),
        ("a:b 1:2", ["a:b", "1", "2"]),
        ("café über", ["café", "über"]),
        ("%% -- ..", []),
    ],
)
def test_tokens_split_like_unicode_words(text, expected):
    assert tokens(text) == expected


def test_find_phrase_matches_whole_words_only():
    text = "Martinez field. Martinville gas. Martin well."

    start, end = find_phrase(text, ["martin"])

    assert text[start:end] == "Martin"
    assert start == text.index("Martin well")


def test_find_phrase_requires_the_words_in_order():
    assert find_phrase("County of Martin", ["martin", "county"]) is None
    assert find_phrase("Martin County", []) is None


# Snippets


def test_a_blank_line_ends_a_statement():
    text = "HEADLINE ABOUT DRILLING\n\nMartin County wildcat spudded\n\nNext item"
    start = text.index("Martin")

    assert snippet(text, start, start + 13)[0] == "Martin County wildcat spudded"


def test_snippet_ends_with_a_closing_quote(repository):
    found(repository, make_match('He said "drill in Martin County." Then left.'))

    assert search(repository, "Martin County").results[0].snippet == (
        'He said "drill in Martin County."'
    )


def test_long_statements_are_cut_at_word_boundaries(repository):
    words = " ".join(f"word{i}" for i in range(200))
    found(repository, make_match(f"{words} Martin County {words}"))

    hit = search(repository, "Martin County").results[0]

    assert hit.snippet.startswith("...word")
    assert hit.snippet.endswith("...")
    assert matched(hit) == "Martin County"
    before = hit.snippet[3 : hit.match_start]
    after = hit.snippet[hit.match_end : -3]
    assert len(before) <= SNIPPET_CONTEXT
    assert len(after) <= SNIPPET_CONTEXT
    # No word is split by the cut.
    assert set(before.split()) <= set(words.split())
    assert set(after.split()) <= set(words.split())


def test_short_passage_without_sentence_ends_is_kept_whole(repository):
    found(repository, make_match("a Martin County b"))

    hit = search(repository, "Martin County").results[0]

    assert hit.snippet == "a Martin County b"
    assert (hit.match_start, hit.match_end) == (2, 15)
