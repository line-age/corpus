"""`python -m corpus.ingestion`, with a mocked archive.org client.

Shares the migrated test database with other test files: each test deletes the rows it
creates and never assumes the tables are otherwise empty.
"""

import json
from collections.abc import Iterator
from unittest.mock import create_autospec
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from corpus.ingestion import __main__ as cli
from corpus.ingestion.client import ArchiveOrgClient, SourceItem
from corpus.ingestion.exceptions import FetchError


def make_source(identifier: str) -> SourceItem:
    return SourceItem(
        identifier=identifier,
        source_url=f"https://archive.example/details/{identifier}",
        metadata={"title": "The Journal"},
        text="CoverPage one",
        page_index=[[0, 5], [5, 13]],
        page_numbers=None,
    )


@pytest.fixture
def identifiers(migrated_db) -> Iterator[list[str]]:
    """Two fresh identifiers; their rows are deleted after the test."""
    values = [f"test-ingest-cli-{uuid4().hex}" for _ in range(2)]
    yield values
    engine = create_engine(migrated_db.database_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM documents WHERE identifier = ANY(:ids)"),
                {"ids": values},
            )
    finally:
        engine.dispose()


@pytest.fixture
def client_class(monkeypatch, configure):
    """The client class the CLI uses, mocked. Settings get a fake base URL, to show the
    client is built from them."""
    configure(
        archive_org_base_url="https://archive.example", ingest_http_timeout_seconds=9
    )
    mock_class = create_autospec(ArchiveOrgClient)
    mock_class.return_value = create_autospec(ArchiveOrgClient, instance=True)
    monkeypatch.setattr(cli, "ArchiveOrgClient", mock_class)
    return mock_class


@pytest.fixture
def client(client_class):
    """The client instance the CLI builds."""
    return client_class.return_value


def page_counts(settings, identifiers: list[str]) -> dict[str, int]:
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT identifier, count(pages.id) FROM documents"
                    " LEFT JOIN pages ON pages.document_id = documents.id"
                    " WHERE identifier = ANY(:ids) GROUP BY identifier"
                ),
                {"ids": identifiers},
            ).all()
    finally:
        engine.dispose()
    return dict(rows)


def log_lines(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_ingests_every_item_and_exits_0(
    migrated_db, client_class, client, identifiers, capsys
):
    client.fetch.side_effect = make_source

    assert cli.main(identifiers) == 0

    client_class.assert_called_once_with("https://archive.example", 9)
    assert page_counts(migrated_db, identifiers) == {
        identifiers[0]: 2,
        identifiers[1]: 2,
    }
    done = [line for line in log_lines(capsys) if line.get("document_id")]
    # Items run concurrently, so they may finish in any order.
    assert sorted((line["identifier"], line["pages"]) for line in done) == sorted(
        [(identifiers[0], 2), (identifiers[1], 2)]
    )


def test_a_failed_item_does_not_stop_the_others_but_exits_1(
    migrated_db, client, identifiers, capsys
):
    def fetch(identifier):
        if identifier == identifiers[0]:
            raise FetchError("GET https://archive.example/... failed: HTTP 503")
        return make_source(identifier)

    client.fetch.side_effect = fetch

    assert cli.main(identifiers) == 1

    assert page_counts(migrated_db, identifiers) == {identifiers[1]: 2}
    errors = [line for line in log_lines(capsys) if line["level"] == "ERROR"]
    assert len(errors) == 1
    assert errors[0]["identifier"] == identifiers[0]
    assert "HTTP 503" in errors[0]["message"]


def test_an_unexpected_error_is_logged_with_its_traceback_and_exits_1(
    migrated_db, client, identifiers, capsys
):
    client.fetch.side_effect = RuntimeError("unexpected")

    assert cli.main(identifiers[:1]) == 1

    (error,) = [line for line in log_lines(capsys) if line["level"] == "ERROR"]
    assert "RuntimeError: unexpected" in error["exception"]


def test_repeated_identifiers_are_ingested_once(migrated_db, client, identifiers):
    client.fetch.side_effect = make_source

    assert cli.main([identifiers[0], identifiers[0]]) == 0

    client.fetch.assert_called_once_with(identifiers[0])


@pytest.mark.parametrize("argv", [["../etc/passwd"], ["ok_id", "a b"], ["a?x=1"], []])
def test_bad_arguments_exit_2_before_anything_runs(client_class, argv, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(argv)

    assert exit_info.value.code == 2
    client_class.assert_not_called()


def test_collection_items_are_listed_sampled_and_ingested(
    migrated_db, client, identifiers, capsys
):
    client.list_collection.return_value = [
        identifiers[0],
        "skipped-a",
        "skipped-b",
        identifiers[1],
    ]
    client.fetch.side_effect = make_source

    argv = ["--collection", "pub_journal", "--to", "1919-12-31", "--sample", "2"]
    assert cli.main(argv) == 0

    client.list_collection.assert_called_once_with(
        "pub_journal", date_from=None, date_to=cli.date(1919, 12, 31)
    )
    assert sorted(call.args[0] for call in client.fetch.call_args_list) == sorted(
        identifiers
    )
    (listed,) = [line for line in log_lines(capsys) if line.get("collection")]
    assert (listed["found"], listed["chosen"]) == (4, 2)


def test_skip_existing_ingests_only_new_items(migrated_db, client, identifiers):
    client.fetch.side_effect = make_source
    assert cli.main(identifiers[:1]) == 0
    client.fetch.reset_mock()

    assert cli.main(["--skip-existing", *identifiers]) == 0

    client.fetch.assert_called_once_with(identifiers[1])
    assert page_counts(migrated_db, identifiers) == {
        identifiers[0]: 2,
        identifiers[1]: 2,
    }


def test_a_collection_that_cannot_be_listed_exits_1_but_items_still_run(
    migrated_db, client, identifiers, capsys
):
    client.list_collection.side_effect = FetchError("GET ... failed: HTTP 502")
    client.fetch.side_effect = make_source

    assert cli.main([identifiers[0], "--collection", "pub_journal"]) == 1

    assert page_counts(migrated_db, identifiers) == {identifiers[0]: 2}
    (error,) = [line for line in log_lines(capsys) if line["level"] == "ERROR"]
    assert error["collection"] == "pub_journal"


@pytest.mark.parametrize(
    "argv",
    [
        ["--sample", "5", "item"],  # --sample without --collection
        ["--collection", "c", "--sample", "0"],
        ["--collection", "c", "--from", "1920-01-01", "--to", "1919-01-01"],
        ["--collection", "c", "--to", "1919"],
        ["--collection", "c d"],
        ["item", "--concurrency", "17"],
        ["item", "--concurrency", "0"],
    ],
)
def test_bad_collection_arguments_exit_2(client_class, argv):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(argv)

    assert exit_info.value.code == 2
    client_class.assert_not_called()


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1, [0]),
        (2, [0, 9]),
        (4, [0, 3, 6, 9]),
        (10, list(range(10))),
        (50, list(range(10))),
    ],
)
def test_evenly_spaced_spans_first_to_last(count, expected):
    assert cli.evenly_spaced(list(range(10)), count) == expected
