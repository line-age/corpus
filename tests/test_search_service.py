"""SearchService against a mocked repository: phrase matching, snippets and paging."""

import asyncio
from datetime import date
from unittest.mock import create_autospec

import pytest

from corpus.api.modules.search.repository import CorpusStats, SearchRepository
from corpus.api.modules.search.service import SNIPPET_CONTEXT, SearchService, bm25
from corpus.models import Document, Page

def make_page(text: str, leaf_number: int = 0, page_number: str | None = None) -> Page:
    document = Document(
        id=1,
        identifier="oilgasjournal-test",
        title="The Oil and Gas Journal",
        publication_date=date(1951, 6, 7),
        volume="50",
        issue="5",
        source_url="https://archive.org/details/oilgasjournal-test",
    )
    return Page(leaf_number=leaf_number, page_number=page_number, text=text, document=document)


@pytest.fixture
def repository():
    """A SearchRepository mock that finds no pages. Autospec limits it to the real class's
    methods and signatures, so a renamed or removed method fails the test."""
    mock = create_autospec(SearchRepository, instance=True)
    mock.find_pages_containing.return_value = []
    return mock


def search(repository, query: str, limit: int = 20, offset: int = 0, *, ranking: bool = False):
    service = SearchService(repository, ranking=ranking)
    return asyncio.run(service.search(query, limit=limit, offset=offset))


def matched(hit) -> str:
    return hit.snippet[hit.match_start : hit.match_end]


def test_no_matches(repository):
    result = search(repository, "Martin County")

    assert result.total == 0
    assert result.results == []
    repository.find_pages_containing.assert_awaited_once_with(["Martin", "County"])


def test_a_candidate_page_without_the_phrase_yields_nothing(repository):
    # The repository matches words anywhere on the page; the service requires the phrase.
    repository.find_pages_containing.return_value = [
        make_page("County roads in Martin are long. Martin, County Clare.")
    ]

    assert search(repository, "Martin County").total == 0


def test_every_occurrence_on_a_page_is_a_result(repository):
    repository.find_pages_containing.return_value = [
        make_page("Martin County well. Another Martin County test. Third in Martin County.")
    ]

    result = search(repository, "Martin County")

    assert result.total == 3
    assert [hit.snippet for hit in result.results] == [
        "Martin County well.",
        "Another Martin County test.",
        "Third in Martin County.",
    ]
    assert all(matched(hit) == "Martin County" for hit in result.results)


def test_phrase_matches_across_a_line_break(repository):
    repository.find_pages_containing.return_value = [
        make_page(
            "Humble Oil has staked location for a northwest Martin\nCounty wildcat. Next."
        )
    ]

    result = search(repository, "Martin County")

    assert result.total == 1
    hit = result.results[0]
    assert hit.snippet == "Humble Oil has staked location for a northwest Martin County wildcat."
    assert matched(hit) == "Martin County"


def test_matches_whole_words_only(repository):
    repository.find_pages_containing.return_value = [
        make_page("Martinez field. Martinville gas. Martin well. (Martin) test.")
    ]

    result = search(repository, "Martin")

    assert [matched(hit) for hit in result.results] == ["Martin", "Martin"]
    assert [hit.snippet for hit in result.results] == ["Martin well.", "(Martin) test."]


def test_matching_ignores_case_and_keeps_the_original_text(repository):
    repository.find_pages_containing.return_value = [make_page("A MARTIN county well.")]

    result = search(repository, "martin COUNTY")

    assert result.total == 1
    assert matched(result.results[0]) == "MARTIN county"


def test_query_regex_characters_are_literal(repository):
    repository.find_pages_containing.return_value = [make_page("Pay is 3.5 ft. Pay is 3x5 ft.")]

    result = search(repository, "3.5 ft")

    assert [matched(hit) for hit in result.results] == ["3.5 ft"]


def test_snippet_is_the_statement_containing_the_match(repository):
    repository.find_pages_containing.return_value = [
        make_page(
            "First sentence here! Then   a\n\tsecond about Martin County,\nwhich ends? "
            "Third.\n\nNew paragraph."
        )
    ]

    hit = search(repository, "Martin County").results[0]

    assert hit.snippet == "Then a second about Martin County, which ends?"
    assert matched(hit) == "Martin County"


def test_a_blank_line_ends_a_statement(repository):
    repository.find_pages_containing.return_value = [
        make_page("HEADLINE ABOUT DRILLING\n\nMartin County wildcat spudded\n\nNext item")
    ]

    hit = search(repository, "Martin County").results[0]

    assert hit.snippet == "Martin County wildcat spudded"


def test_snippet_ends_with_a_closing_quote(repository):
    repository.find_pages_containing.return_value = [
        make_page('He said "drill in Martin County." Then left.')
    ]

    assert search(repository, "Martin County").results[0].snippet == (
        'He said "drill in Martin County."'
    )


def test_long_statements_are_cut_at_word_boundaries(repository):
    words = " ".join(f"word{i}" for i in range(200))
    repository.find_pages_containing.return_value = [
        make_page(f"{words} Martin County {words}")
    ]

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


def test_short_page_without_sentence_ends_is_kept_whole(repository):
    repository.find_pages_containing.return_value = [make_page("a Martin County b")]

    hit = search(repository, "Martin County").results[0]

    assert hit.snippet == "a Martin County b"
    assert (hit.match_start, hit.match_end) == (2, 15)


def test_citation_links_to_the_page(repository):
    repository.find_pages_containing.return_value = [
        make_page("Martin County.", leaf_number=148, page_number="145")
    ]

    hit = search(repository, "Martin County").results[0]

    assert hit.document.identifier == "oilgasjournal-test"
    assert hit.document.publication_date == date(1951, 6, 7)
    assert hit.page.leaf_number == 148
    assert hit.page.page_number == "145"
    assert hit.page.url == "https://archive.org/details/oilgasjournal-test/page/n148"


def test_offset_and_limit_page_through_all_occurrences(repository):
    repository.find_pages_containing.return_value = [
        make_page("Martin one. Martin two.", leaf_number=1),
        make_page("Martin three. Martin four. Martin five.", leaf_number=2),
    ]

    result = search(repository, "Martin", limit=2, offset=1)

    assert result.total == 5
    assert (result.limit, result.offset) == (2, 1)
    assert [hit.snippet for hit in result.results] == ["Martin two.", "Martin three."]
    assert [hit.page.leaf_number for hit in result.results] == [1, 2]


def test_offset_past_the_end_returns_the_total_and_no_results(repository):
    repository.find_pages_containing.return_value = [make_page("Martin one. Martin two.")]

    result = search(repository, "Martin", offset=5)

    assert result.total == 2
    assert result.results == []


# Ranking (the SEARCH_RANKING_ENABLED feature flag)

STATS = CorpusStats(pages=1000, average_page_length=100.0)


def test_without_ranking_results_are_in_corpus_order_without_scores(repository):
    repository.find_pages_containing.return_value = [
        make_page("Tulsa once.", leaf_number=1),
        make_page("Tulsa, Tulsa and Tulsa again.", leaf_number=2),
    ]

    result = search(repository, "Tulsa")

    assert result.order == "corpus"
    assert [hit.page.leaf_number for hit in result.results] == [1, 2, 2, 2]
    assert {hit.score for hit in result.results} == {None}
    repository.get_corpus_stats.assert_not_awaited()


def test_ranking_puts_the_page_with_more_occurrences_first(repository):
    repository.get_corpus_stats.return_value = STATS
    repository.find_pages_containing.return_value = [
        make_page("Tulsa once, among other news of the day.", leaf_number=1),
        make_page("Tulsa, Tulsa and Tulsa again, all day.", leaf_number=2),
    ]

    result = search(repository, "Tulsa", ranking=True)

    assert result.order == "relevance"
    assert [hit.page.leaf_number for hit in result.results] == [2, 2, 2, 1]
    scores = [hit.score for hit in result.results]
    assert scores == sorted(scores, reverse=True)
    assert len(set(scores[:3])) == 1  # occurrences on one page share its score
    assert scores[0] > scores[3]


def test_ranking_puts_the_shorter_page_first_when_counts_are_equal(repository):
    repository.get_corpus_stats.return_value = STATS
    repository.find_pages_containing.return_value = [
        make_page("Tulsa " + "and other news " * 40, leaf_number=1),
        make_page("Tulsa news.", leaf_number=2),
    ]

    result = search(repository, "Tulsa", ranking=True)

    assert [hit.page.leaf_number for hit in result.results] == [2, 1]


def test_ranking_keeps_corpus_order_for_equal_scores(repository):
    repository.get_corpus_stats.return_value = STATS
    repository.find_pages_containing.return_value = [
        make_page("Tulsa news. More Tulsa.", leaf_number=leaf) for leaf in (5, 3, 9)
    ]

    result = search(repository, "Tulsa", ranking=True)

    # The repository's order (here 5, 3, 9) is corpus order; within a page, position.
    assert [hit.page.leaf_number for hit in result.results] == [5, 5, 3, 3, 9, 9]
    assert [hit.snippet for hit in result.results] == ["Tulsa news.", "More Tulsa."] * 3


def test_ranking_pages_after_sorting(repository):
    repository.get_corpus_stats.return_value = STATS
    repository.find_pages_containing.return_value = [
        make_page("Tulsa once, among other news of the day.", leaf_number=1),
        make_page("Tulsa, Tulsa and Tulsa again, all day.", leaf_number=2),
    ]

    result = search(repository, "Tulsa", limit=2, offset=2, ranking=True)

    assert result.total == 4
    assert [hit.page.leaf_number for hit in result.results] == [2, 1]


def test_ranking_without_matches_skips_the_corpus_stats(repository):
    result = search(repository, "Tulsa", ranking=True)

    assert (result.order, result.total) == ("relevance", 0)
    repository.get_corpus_stats.assert_not_awaited()


def test_bm25_grows_with_occurrences_but_with_diminishing_returns():
    one, two, three = (bm25(n, 100, 10, STATS) for n in (1, 2, 3))

    assert 0 < one < two < three
    assert three - two < two - one


def test_bm25_falls_as_pages_get_longer():
    assert bm25(1, 50, 10, STATS) > bm25(1, 100, 10, STATS) > bm25(1, 400, 10, STATS)


def test_bm25_is_higher_for_a_phrase_on_fewer_pages():
    assert bm25(1, 100, 5, STATS) > bm25(1, 100, 500, STATS) > 0


def test_bm25_handles_a_corpus_of_empty_pages():
    empty = CorpusStats(pages=1, average_page_length=0.0)

    assert bm25(1, 0, 1, empty) > 0
