"""ArchiveOrgClient against a fake urlopen: no network."""

import gzip
import io
import json
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from corpus import __version__
from corpus.ingestion import client as client_module
from corpus.ingestion.client import ArchiveOrgClient, is_valid_identifier
from corpus.ingestion.exceptions import FetchError

BASE = "https://archive.example"
ID = "journal_1951-06-07_50_5"
DOWNLOAD = f"{BASE}/download/{ID}/{ID}"


class FakeArchive:
    """Stands in for urllib's urlopen: serves `responses` by URL and records requests.
    A response that is an exception is raised instead."""

    def __init__(self, responses: dict[str, bytes | Exception]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, dict[str, str], float]] = []

    def __call__(self, request, timeout):
        self.requests.append((request.full_url, dict(request.header_items()), timeout))
        response = self.responses.get(request.full_url)
        if isinstance(response, list):  # successive responses to repeated requests
            response = response.pop(0)
        if response is None:
            response = HTTPError(request.full_url, 404, "Not Found", None, None)
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(response)


@pytest.fixture(autouse=True)
def sleeps(monkeypatch) -> list[float]:
    """Retry delays are recorded instead of slept."""
    slept: list[float] = []
    monkeypatch.setattr(client_module, "sleep", slept.append)
    return slept


def item_files(
    overrides: dict[str, bytes | Exception | list] | None = None,
) -> dict[str, bytes | Exception]:
    """A complete item by URL. `overrides` replaces files by suffix, or "metadata"."""
    files: dict[str, bytes | Exception] = {
        "metadata": json.dumps({"metadata": {"title": "The Journal"}}).encode(),
        "_hocr_searchtext.txt.gz": gzip.compress("Cover\nPage one ✓".encode()),
        "_hocr_pageindex.json.gz": gzip.compress(b"[[0, 6, 0, 10], [6, 16, 10, 20]]"),
        "_page_numbers.json": b'{"pages": []}',
    } | (overrides or {})
    return {
        (f"{BASE}/metadata/{ID}" if name == "metadata" else f"{DOWNLOAD}{name}"): body
        for name, body in files.items()
    }


@pytest.fixture
def archive(monkeypatch):
    fake = FakeArchive(item_files())
    monkeypatch.setattr(client_module, "urlopen", fake)
    return fake


def test_fetch_downloads_and_decodes_every_file(archive):
    item = ArchiveOrgClient(BASE, timeout=7).fetch(ID)

    assert item.identifier == ID
    assert item.source_url == f"{BASE}/details/{ID}"
    assert item.metadata == {"title": "The Journal"}
    assert item.text == "Cover\nPage one ✓"
    assert item.page_index == [[0, 6, 0, 10], [6, 16, 10, 20]]
    assert item.page_numbers == {"pages": []}


def test_requests_use_the_timeout_and_a_generic_user_agent(archive):
    ArchiveOrgClient(BASE, timeout=7).fetch(ID)

    assert len(archive.requests) == 4
    for _, headers, timeout in archive.requests:
        assert headers["User-agent"] == f"corpus-ingest/{__version__}"
        assert timeout == 7


def test_a_trailing_slash_on_the_base_url_is_ignored(archive):
    item = ArchiveOrgClient(BASE + "/", timeout=1).fetch(ID)

    assert item.source_url == f"{BASE}/details/{ID}"


def test_an_item_without_page_numbers_has_none(archive):
    del archive.responses[f"{DOWNLOAD}_page_numbers.json"]

    assert ArchiveOrgClient(BASE, timeout=1).fetch(ID).page_numbers is None


@pytest.mark.parametrize(
    "error",
    [
        HTTPError("url", 503, "Service Unavailable", None, None),
        HTTPError(
            "url", 404, "Not Found", None, None
        ),  # only page numbers may be missing
        URLError("Name or service not known"),
        TimeoutError("timed out"),
    ],
)
def test_download_failures_raise_fetch_error(monkeypatch, error):
    monkeypatch.setattr(
        client_module,
        "urlopen",
        FakeArchive(item_files({"_hocr_searchtext.txt.gz": error})),
    )

    with pytest.raises(FetchError, match="_hocr_searchtext.txt.gz failed"):
        ArchiveOrgClient(BASE, timeout=1).fetch(ID)


def test_an_unknown_item_raises_fetch_error(monkeypatch):
    # archive.org answers an unknown identifier with 200 and an empty record.
    monkeypatch.setattr(
        client_module, "urlopen", FakeArchive(item_files({"metadata": b"{}"}))
    )

    with pytest.raises(FetchError, match="has no item"):
        ArchiveOrgClient(BASE, timeout=1).get_metadata(ID)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"_hocr_searchtext.txt.gz": b"not gzip"}, "not valid gzip"),
        ({"_hocr_searchtext.txt.gz": gzip.compress(b"\xff\xfe")}, "not valid UTF-8"),
        ({"_hocr_pageindex.json.gz": gzip.compress(b"[[0,")}, "not valid JSON"),
        ({"_page_numbers.json": b"<html>"}, "not valid JSON"),
    ],
)
def test_undecodable_files_raise_fetch_error(monkeypatch, overrides, message):
    monkeypatch.setattr(client_module, "urlopen", FakeArchive(item_files(overrides)))

    with pytest.raises(FetchError, match=message):
        ArchiveOrgClient(BASE, timeout=1).fetch(ID)


@pytest.mark.parametrize("identifier", ["../etc", "a/b", "a b", "a?b=c", "", "a\n"])
def test_unsafe_identifiers_are_rejected_before_any_request(archive, identifier):
    assert not is_valid_identifier(identifier)
    with pytest.raises(ValueError, match="not an archive.org identifier"):
        ArchiveOrgClient(BASE, timeout=1).fetch(identifier)
    assert archive.requests == []


class FakeSearch:
    """Stands in for urlopen on the advanced search API: serves `identifiers` in pages
    of `rows` and records each request's query parameters."""

    def __init__(self, identifiers: list[str], *, total: int | None = None) -> None:
        self.identifiers = identifiers
        self.total = len(identifiers) if total is None else total
        self.queries: list[dict[str, list[str]]] = []

    def __call__(self, request, timeout):
        url = urlsplit(request.full_url)
        assert f"{url.scheme}://{url.netloc}{url.path}" == f"{BASE}/advancedsearch.php"
        params = parse_qs(url.query)
        self.queries.append(params)
        rows, page = int(params["rows"][0]), int(params["page"][0])
        docs = [
            {"identifier": i} for i in self.identifiers[(page - 1) * rows : page * rows]
        ]
        body = {"response": {"numFound": self.total, "docs": docs}}
        return io.BytesIO(json.dumps(body).encode())


def test_list_collection_pages_through_every_item_oldest_first(monkeypatch):
    items = [f"item-{n}" for n in range(5)]
    search = FakeSearch(items)
    monkeypatch.setattr(client_module, "urlopen", search)
    monkeypatch.setattr(client_module, "SEARCH_PAGE_SIZE", 2)

    assert ArchiveOrgClient(BASE, timeout=7).list_collection("pub_journal") == items

    assert [q["page"] for q in search.queries] == [["1"], ["2"], ["3"]]
    assert search.queries[0]["q"] == ["collection:pub_journal"]
    assert search.queries[0]["sort[]"] == ["date asc", "identifier asc"]


@pytest.mark.parametrize(
    ("bounds", "query"),
    [
        ({"date_to": date(1919, 12, 31)}, "collection:c AND date:[* TO 1919-12-31]"),
        ({"date_from": date(1902, 1, 1)}, "collection:c AND date:[1902-01-01 TO *]"),
        (
            {"date_from": date(1902, 1, 1), "date_to": date(1910, 6, 30)},
            "collection:c AND date:[1902-01-01 TO 1910-06-30]",
        ),
    ],
)
def test_list_collection_filters_by_date(monkeypatch, bounds, query):
    search = FakeSearch(["a"])
    monkeypatch.setattr(client_module, "urlopen", search)

    ArchiveOrgClient(BASE, timeout=7).list_collection("c", **bounds)

    assert search.queries[0]["q"] == [query]


def test_list_collection_stops_when_a_page_is_empty(monkeypatch):
    # A total larger than what the API actually returns must not loop forever.
    search = FakeSearch(["a", "b"], total=10)
    monkeypatch.setattr(client_module, "urlopen", search)

    assert ArchiveOrgClient(BASE, timeout=7).list_collection("c") == ["a", "b"]


def test_list_collection_rejects_an_unexpected_response(monkeypatch):
    monkeypatch.setattr(
        client_module, "urlopen", lambda request, timeout: io.BytesIO(b"{}")
    )

    with pytest.raises(FetchError, match="not an advanced search response"):
        ArchiveOrgClient(BASE, timeout=7).list_collection("c")


@pytest.mark.parametrize("collection", ["a b", "c) OR (x", "../c"])
def test_list_collection_rejects_unsafe_names_before_any_request(archive, collection):
    with pytest.raises(ValueError):
        ArchiveOrgClient(BASE, timeout=7).list_collection(collection)

    assert archive.requests == []


def http_error(code: int) -> HTTPError:
    return HTTPError("url", code, "error", None, None)


@pytest.mark.parametrize(
    "transient",
    [http_error(500), http_error(503), http_error(429), TimeoutError("timed out")],
)
def test_transient_failures_are_retried(monkeypatch, sleeps, transient):
    text = gzip.compress(b"Cover\nPage one")
    files = item_files({"_hocr_searchtext.txt.gz": [transient, transient, text]})
    archive = FakeArchive(files)
    monkeypatch.setattr(client_module, "urlopen", archive)

    assert ArchiveOrgClient(BASE, timeout=1).get_search_text(ID) == "Cover\nPage one"

    assert sleeps == list(client_module.RETRY_DELAYS)
    assert len(archive.requests) == 3


def test_retries_give_up_after_the_last_delay(monkeypatch, sleeps):
    failures = [http_error(502)] * (len(client_module.RETRY_DELAYS) + 1)
    monkeypatch.setattr(
        client_module,
        "urlopen",
        FakeArchive(item_files({"_hocr_searchtext.txt.gz": failures})),
    )

    with pytest.raises(FetchError, match="HTTP 502"):
        ArchiveOrgClient(BASE, timeout=1).get_search_text(ID)

    assert sleeps == list(client_module.RETRY_DELAYS)


@pytest.mark.parametrize("code", [400, 403, 404])
def test_client_errors_are_not_retried(monkeypatch, sleeps, code):
    archive = FakeArchive(item_files({"_hocr_searchtext.txt.gz": http_error(code)}))
    monkeypatch.setattr(client_module, "urlopen", archive)

    with pytest.raises(FetchError, match=f"HTTP {code}"):
        ArchiveOrgClient(BASE, timeout=1).get_search_text(ID)

    assert sleeps == []
    assert len(archive.requests) == 1
