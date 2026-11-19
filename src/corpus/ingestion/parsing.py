"""Turns a downloaded item into the document and pages ingest stores.

Pure functions, no I/O. Anything inconsistent raises InvalidSourceData, so a bad item is
rejected whole rather than stored with pages out of line with the scan.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from corpus.ingestion.client import SourceItem
from corpus.ingestion.exceptions import InvalidSourceData

# Column sizes in corpus.models; longer values are rejected here with a clear message
# rather than by the database.
MAX_VOLUME_LENGTH = 50
MAX_ISSUE_LENGTH = 50
MAX_PAGE_NUMBER_LENGTH = 20

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class DocumentFields:
    identifier: str
    title: str
    publication_date: date | None
    volume: str | None
    issue: str | None
    source_url: str


@dataclass(frozen=True)
class PageFields:
    leaf_number: int
    """0-based position in the scan."""
    page_number: str | None
    """The printed page number, or None when none was detected."""
    text: str


@dataclass(frozen=True)
class ParsedItem:
    document: DocumentFields
    pages: list[PageFields]


def parse_item(source: SourceItem) -> ParsedItem:
    """The document and its pages, one per leaf in the page index."""
    texts = split_pages(source.text, source.page_index)
    numbers = parse_page_numbers(source.page_numbers, len(texts))
    pages = [
        PageFields(leaf_number=leaf, page_number=number, text=text)
        for leaf, (text, number) in enumerate(zip(texts, numbers, strict=True))
    ]
    return ParsedItem(document=parse_document(source), pages=pages)


def split_pages(text: str, page_index: Any) -> list[str]:
    """Each leaf's text, in leaf order, cut from `text` at the index's character offsets.

    Each entry starts [text_start, text_end, ...]. Offsets must lie within the text, and
    pages must not overlap or run backwards. Surrounding whitespace is trimmed, and NUL
    characters, which Postgres text columns cannot hold, are dropped. A leaf with no
    text, such as a blank page, keeps its row with empty text.
    """
    if not isinstance(page_index, list) or not page_index:
        raise InvalidSourceData("page index is not a non-empty list")
    pages = []
    previous_end = 0
    for leaf, entry in enumerate(page_index):
        if (
            not isinstance(entry, list)
            or len(entry) < 2
            or not all(_is_int(offset) for offset in entry[:2])
        ):
            raise InvalidSourceData(f"page index entry {leaf} is not [start, end, ...]: {entry!r}")
        start, end = entry[0], entry[1]
        if not previous_end <= start <= end <= len(text):
            raise InvalidSourceData(
                f"page index entry {leaf} has offsets {start}..{end}, outside "
                f"{previous_end}..{len(text)} (text length {len(text)})"
            )
        pages.append(text[start:end].replace("\x00", "").strip())
        previous_end = end
    return pages


def parse_page_numbers(page_numbers: Any | None, leaf_count: int) -> list[str | None]:
    """The printed page number of each leaf, in leaf order: None where none was detected,
    and for every leaf when the item has no page numbers file.

    The file is {"pages": [{"leafNum": 0, "pageNumber": "", ...}, ...]} with one entry
    per leaf. An empty pageNumber means none was detected.
    """
    if page_numbers is None:
        return [None] * leaf_count
    entries = page_numbers.get("pages") if isinstance(page_numbers, dict) else None
    if not isinstance(entries, list):
        raise InvalidSourceData("page numbers file has no list of pages")
    if len(entries) != leaf_count:
        raise InvalidSourceData(
            f"page numbers file has {len(entries)} pages but the page index has {leaf_count}"
        )
    numbers: list[str | None] = [None] * leaf_count
    seen: set[int] = set()
    for entry in entries:
        leaf = entry.get("leafNum") if isinstance(entry, dict) else None
        if not _is_int(leaf) or not 0 <= leaf < leaf_count or leaf in seen:
            raise InvalidSourceData(f"page numbers entry has a bad or repeated leafNum: {entry!r}")
        seen.add(leaf)
        number = entry.get("pageNumber")
        if number is None or number == "":
            continue
        if not isinstance(number, str) or len(number) > MAX_PAGE_NUMBER_LENGTH:
            raise InvalidSourceData(f"leaf {leaf} has an unusable pageNumber: {number!r}")
        numbers[leaf] = number
    return numbers


def parse_document(source: SourceItem) -> DocumentFields:
    """Document fields from the item's metadata. The title falls back to the identifier;
    a date that is not a full ISO date (YYYY-MM-DD), such as "1951-06", becomes None."""
    metadata = source.metadata
    title = _collapse_whitespace(_first_string(metadata.get("title")))
    return DocumentFields(
        identifier=source.identifier,
        title=title or source.identifier,
        publication_date=parse_date(_first_string(metadata.get("date"))),
        volume=_bounded(metadata, "volume", MAX_VOLUME_LENGTH),
        issue=_bounded(metadata, "issue", MAX_ISSUE_LENGTH),
        source_url=source.source_url,
    )


def parse_date(value: str | None) -> date | None:
    """`value` as a date if it is exactly YYYY-MM-DD and a real day, otherwise None."""
    if value is None or not _ISO_DATE.fullmatch(value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:  # e.g. 1951-02-30
        return None


def _bounded(metadata: dict[str, Any], key: str, max_length: int) -> str | None:
    value = _collapse_whitespace(_first_string(metadata.get(key)))
    if value and len(value) > max_length:
        raise InvalidSourceData(f"metadata {key} is longer than {max_length} characters")
    return value or None


def _first_string(value: Any) -> str | None:
    """archive.org metadata fields are a string, or a list of strings when repeated."""
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _collapse_whitespace(value: str | None) -> str | None:
    return None if value is None else _WHITESPACE.sub(" ", value).strip()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
