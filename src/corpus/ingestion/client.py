"""Downloads an archive.org item's metadata and OCR files.

archive.org's plain-text OCR (`_djvu.txt`) has no page breaks, so ingest reads the same
OCR from the hOCR derivatives, which record where each page's text starts and ends:

- `<id>_hocr_searchtext.txt.gz`  the whole item's text, gzipped UTF-8
- `<id>_hocr_pageindex.json.gz`  one entry per scanned page (leaf): character offsets
                                 into that text, then byte offsets into the hOCR file
- `<id>_page_numbers.json`       the page number printed on each leaf, when detected.
                                 Not every item has it.

plus the item's metadata from `/metadata/<id>`. A collection's items are listed with the
advanced search API (`/advancedsearch.php`). Only this module touches the network.
"""

import gzip
import json
import re
from dataclasses import dataclass
from datetime import date
from time import sleep
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from corpus import __version__
from corpus.ingestion.exceptions import FetchError

IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9._-]+")
"""What an archive.org identifier may contain. Anything else could change the URLs built
from it, so it is rejected before any request."""

USER_AGENT = f"corpus-ingest/{__version__}"

RETRY_DELAYS = (2.0, 8.0)
"""Seconds to wait before each retry of a request that failed transiently: a network
error, a timeout, HTTP 429 or a 5xx. archive.org has short outages under load."""
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

SEARCH_PAGE_SIZE = 1000
"""Items requested per page when listing a collection."""


def is_valid_identifier(identifier: str) -> bool:
    """Whether `identifier` is safe to put in an archive.org URL."""
    return IDENTIFIER_PATTERN.fullmatch(identifier) is not None


@dataclass(frozen=True)
class SourceItem:
    """Everything ingest downloads for one item, decoded but not yet checked."""

    identifier: str
    source_url: str
    """The item's page on archive.org, where a reader can view the scan."""
    metadata: dict[str, Any]
    """The `metadata` object of the item's metadata record."""
    text: str
    """The OCR text of the whole item."""
    page_index: Any
    """The decoded page index: a list with one [text_start, text_end, ...] per leaf."""
    page_numbers: Any | None
    """The decoded page numbers file, or None when the item has none."""


class ArchiveOrgClient:
    """Fetches items from archive.org, or from a mirror at `base_url`."""

    def __init__(self, base_url: str, timeout: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def fetch(self, identifier: str) -> SourceItem:
        """Download everything ingest needs for one item."""
        return SourceItem(
            identifier=identifier,
            source_url=self.details_url(identifier),
            metadata=self.get_metadata(identifier),
            text=self.get_search_text(identifier),
            page_index=self.get_page_index(identifier),
            page_numbers=self.get_page_numbers(identifier),
        )

    def details_url(self, identifier: str) -> str:
        """The item's page on archive.org."""
        return f"{self._base_url}/details/{self._path(identifier)}"

    def get_metadata(self, identifier: str) -> dict[str, Any]:
        """The item's `metadata` object. archive.org answers an unknown identifier with
        an empty record rather than a 404, so that is reported as missing here."""
        url = f"{self._base_url}/metadata/{self._path(identifier)}"
        record = _decode_json(self._get(url), url)
        metadata = record.get("metadata") if isinstance(record, dict) else None
        if not isinstance(metadata, dict):
            raise FetchError(f"archive.org has no item {identifier!r}")
        return metadata

    def list_collection(
        self, collection: str, *, date_from: date | None = None, date_to: date | None = None
    ) -> list[str]:
        """Identifiers of the items in `collection`, oldest first, optionally only those
        dated from `date_from` to `date_to` inclusive."""
        if not is_valid_identifier(collection):
            raise ValueError(f"not an archive.org collection: {collection!r}")
        query = f"collection:{collection}"
        if date_from or date_to:
            start = date_from.isoformat() if date_from else "*"
            end = date_to.isoformat() if date_to else "*"
            query += f" AND date:[{start} TO {end}]"
        identifiers: list[str] = []
        page = 1
        while True:
            params = [
                ("q", query),
                ("fl[]", "identifier"),
                ("sort[]", "date asc"),
                ("sort[]", "identifier asc"),
                ("rows", SEARCH_PAGE_SIZE),
                ("page", page),
                ("output", "json"),
            ]
            url = f"{self._base_url}/advancedsearch.php?{urlencode(params)}"
            total, docs = _search_results(_decode_json(self._get(url), url), url)
            identifiers.extend(docs)
            if not docs or len(identifiers) >= total:
                return identifiers
            page += 1

    def get_search_text(self, identifier: str) -> str:
        url = self._download_url(identifier, "_hocr_searchtext.txt.gz")
        return _decode_text(_gunzip(self._get(url), url), url)

    def get_page_index(self, identifier: str) -> Any:
        url = self._download_url(identifier, "_hocr_pageindex.json.gz")
        return _decode_json(_gunzip(self._get(url), url), url)

    def get_page_numbers(self, identifier: str) -> Any | None:
        """The decoded page numbers file, or None if the item has none (HTTP 404)."""
        url = self._download_url(identifier, "_page_numbers.json")
        body = self._get(url, missing_ok=True)
        return None if body is None else _decode_json(body, url)

    def _download_url(self, identifier: str, suffix: str) -> str:
        path = self._path(identifier)
        return f"{self._base_url}/download/{path}/{path}{suffix}"

    @staticmethod
    def _path(identifier: str) -> str:
        if not is_valid_identifier(identifier):
            raise ValueError(f"not an archive.org identifier: {identifier!r}")
        return quote(identifier, safe="")

    def _get(self, url: str, *, missing_ok: bool = False) -> bytes | None:
        """The response body, or None for a 404 when `missing_ok`. Redirects (archive.org
        sends downloads to a storage node) are followed, and transient failures are
        retried after each of RETRY_DELAYS."""
        for delay in (*RETRY_DELAYS, None):
            try:
                return self._get_once(url, missing_ok=missing_ok)
            except _TransientFetchError as e:
                if delay is None:
                    raise FetchError(str(e)) from e.__cause__
                sleep(delay)
        raise AssertionError("unreachable")

    def _get_once(self, url: str, *, missing_ok: bool) -> bytes | None:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(request, timeout=self._timeout) as response:
                return response.read()
        except HTTPError as e:
            if missing_ok and e.code == 404:
                return None
            error = _TransientFetchError if e.code in RETRYABLE_STATUS else FetchError
            raise error(f"GET {url} failed: HTTP {e.code}") from e
        except (OSError, HTTPException) as e:
            # URLError (DNS, refused connections), timeouts and broken reads.
            reason = getattr(e, "reason", None) or e
            raise _TransientFetchError(f"GET {url} failed: {type(e).__name__}: {reason}") from e


class _TransientFetchError(FetchError):
    """A failure worth retrying. Raised only inside the client."""

def _search_results(body: Any, url: str) -> tuple[int, list[str]]:
    """The total hit count and this page's identifiers from an advanced search response."""
    try:
        response = body["response"]
        total = int(response["numFound"])
        identifiers = [str(doc["identifier"]) for doc in response["docs"]]
    except (KeyError, TypeError, ValueError) as e:
        raise FetchError(f"{url} is not an advanced search response: {e!r}") from e
    return total, identifiers


def _gunzip(body: bytes | None, url: str) -> bytes:
    try:
        return gzip.decompress(body or b"")
    except (OSError, EOFError) as e:  # BadGzipFile is an OSError
        raise FetchError(f"{url} is not valid gzip: {e}") from e


def _decode_text(body: bytes, url: str) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as e:
        raise FetchError(f"{url} is not valid UTF-8: {e}") from e


def _decode_json(body: bytes | None, url: str) -> Any:
    try:
        return json.loads(body or b"")
    except ValueError as e:  # JSONDecodeError and UnicodeDecodeError
        raise FetchError(f"{url} is not valid JSON: {e}") from e
