"""The pure parsing step, on small in-memory items."""

from datetime import date

import pytest

from corpus.ingestion.client import SourceItem
from corpus.ingestion.exceptions import InvalidSourceData
from corpus.ingestion.parsing import (
    parse_date,
    parse_document,
    parse_item,
    parse_page_numbers,
    split_pages,
)

TEXT = " \nCover\n" + "Page one\n" + "Page two\x00\n"


def source(**overrides) -> SourceItem:
    fields = {
        "identifier": "journal_1951",
        "source_url": "https://archive.example/details/journal_1951",
        "metadata": {
            "title": "The  Journal\n 1951-06-07: Vol 50 Iss 5",
            "date": "1951-06-07",
            "volume": "50",
            "issue": "5",
        },
        "text": TEXT,
        "page_index": [[0, 8, 0, 100], [8, 17, 100, 200], [17, 27, 200, 300]],
        "page_numbers": {
            "pages": [
                {"leafNum": 0, "pageNumber": ""},
                {"leafNum": 1, "pageNumber": "1"},
                {"leafNum": 2, "pageNumber": "2"},
            ]
        },
    }
    return SourceItem(**(fields | overrides))


def test_parse_item_gives_one_page_per_leaf_with_its_printed_number():
    item = parse_item(source())

    assert [(p.leaf_number, p.page_number, p.text) for p in item.pages] == [
        (0, None, "Cover"),
        (1, "1", "Page one"),
        (2, "2", "Page two"),
    ]
    assert item.document.identifier == "journal_1951"
    assert item.document.source_url == "https://archive.example/details/journal_1951"


def test_split_pages_trims_whitespace_drops_nul_and_keeps_blank_pages():
    assert split_pages(" ab\x00c \n", [[0, 6], [6, 6], [6, 7]]) == ["abc", "", ""]


def test_split_pages_allows_gaps_between_pages():
    assert split_pages("aaXXbb", [[0, 2], [4, 6]]) == ["aa", "bb"]


@pytest.mark.parametrize(
    "index",
    [
        [[0, 8], [8, 99]],  # past the end of the text
        [[0, 8], [5, 10]],  # overlaps the previous page
        [[0, 8], [10, 9]],  # ends before it starts
        [[-1, 3]],  # negative
    ],
)
def test_split_pages_rejects_bad_offsets(index):
    with pytest.raises(InvalidSourceData, match="offsets"):
        split_pages("x" * 20, index)


@pytest.mark.parametrize(
    "index", [[], {"0": [0, 1]}, None, [[0]], [[0, "5"]], [[True, 5]], ["0,5"]]
)
def test_split_pages_rejects_a_malformed_index(index):
    with pytest.raises(InvalidSourceData):
        split_pages("x" * 20, index)


def test_without_a_page_numbers_file_no_page_has_a_number():
    assert parse_page_numbers(None, 3) == [None, None, None]


def test_page_numbers_are_matched_by_leaf_number_not_position():
    numbers = {
        "pages": [{"leafNum": 1, "pageNumber": "ii"}, {"leafNum": 0, "pageNumber": "i"}]
    }

    assert parse_page_numbers(numbers, 2) == ["i", "ii"]


def test_page_numbers_count_must_match_the_page_index():
    numbers = {"pages": [{"leafNum": 0, "pageNumber": "1"}]}

    with pytest.raises(InvalidSourceData, match="1 pages but the page index has 2"):
        parse_page_numbers(numbers, 2)


@pytest.mark.parametrize(
    "pages",
    [
        [
            {"leafNum": 0, "pageNumber": "1"},
            {"leafNum": 0, "pageNumber": "2"},
        ],  # repeated
        [
            {"leafNum": 0, "pageNumber": "1"},
            {"leafNum": 2, "pageNumber": "2"},
        ],  # out of range
        [{"leafNum": 0, "pageNumber": "1"}, {"pageNumber": "2"}],  # missing
        [{"leafNum": 0, "pageNumber": "1"}, "2"],  # not an object
    ],
)
def test_page_numbers_reject_bad_leaf_numbers(pages):
    with pytest.raises(InvalidSourceData, match="leafNum"):
        parse_page_numbers({"pages": pages}, 2)


@pytest.mark.parametrize("numbers", [{}, {"pages": "1,2"}, [], "pages"])
def test_page_numbers_reject_a_malformed_file(numbers):
    with pytest.raises(InvalidSourceData, match="no list of pages"):
        parse_page_numbers(numbers, 2)


@pytest.mark.parametrize("number", ["x" * 21, 5])
def test_page_numbers_reject_unusable_numbers(number):
    with pytest.raises(InvalidSourceData, match="pageNumber"):
        parse_page_numbers({"pages": [{"leafNum": 0, "pageNumber": number}]}, 1)


def test_parse_item_rejects_page_numbers_that_do_not_match_the_index():
    with pytest.raises(InvalidSourceData):
        parse_item(source(page_numbers={"pages": [{"leafNum": 0, "pageNumber": ""}]}))


def test_document_fields_come_from_the_metadata():
    document = parse_document(source())

    assert document.title == "The Journal 1951-06-07: Vol 50 Iss 5"
    assert document.publication_date == date(1951, 6, 7)
    assert document.volume == "50"
    assert document.issue == "5"


def test_repeated_metadata_fields_use_the_first_value():
    document = parse_document(
        source(metadata={"title": ["First", "Second"], "volume": ["7"]})
    )

    assert document.title == "First"
    assert document.volume == "7"


def test_missing_metadata_fields_become_none_and_the_title_falls_back_to_the_identifier():
    document = parse_document(source(metadata={"title": "  ", "volume": ""}))

    assert document.title == "journal_1951"
    assert document.publication_date is None
    assert document.volume is None
    assert document.issue is None


def test_metadata_too_long_for_its_column_is_rejected():
    with pytest.raises(InvalidSourceData, match="volume"):
        parse_document(source(metadata={"volume": "x" * 51}))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1951-06-07", date(1951, 6, 7)),
        (" 1951-06-07 ", date(1951, 6, 7)),
        ("1951-06", None),
        ("1951", None),
        ("19510607", None),
        ("June 7, 1951", None),
        ("1951-02-30", None),
        ("1951-06-07T00:00:00", None),
        ("", None),
        (None, None),
    ],
)
def test_only_full_iso_dates_are_parsed(value, expected):
    assert parse_date(value) == expected
