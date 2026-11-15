"""End to end over a real Postgres: every stage on two mocked Internet Archive items.

Run with CORPUS_TEST_DATABASE_URL pointing at an empty database. The BM25 step needs the
pg_search extension (ParadeDB); without it that step is skipped and everything else still runs.
"""

import hashlib
import json
from pathlib import Path

import pytest
import respx
from alembic import command
from alembic.config import Config
from corpus_core import db
from corpus_core.ia import IAClient
from corpus_core.storage import LocalStore
from corpus_ingest import stages as S
from corpus_ingest.repo import Repo
from sqlalchemy import create_engine, func, insert, select, text

pytestmark = pytest.mark.db

ROOT = Path(__file__).resolve().parents[1]

PGJ = "pub_pipeline-gas-journal"
BASE = "https://archive.org"
ITEMS = [
    {
        "identifier": "sim_pipeline-gas-journal_1925-06-06_122_23",
        "title": "American Gas Journal 1925-06-06: Vol 122 Iss 23",
        "date": "1925-06-06T00:00:00Z",
        "publisher": "Oildom Publishing Company of Texas, Inc.",
        "volume": "122",
        "issue": "23",
    },
    {
        "identifier": "sim_pipeline-gas-journal_1956-02_46",
        "title": "American Gas Merchandising 1956-02: Iss 46",
        "date": "1956-02-01T00:00:00Z",
        "publisher": "Oildom Publishing Company of Texas, Inc.",
        "issue": "46",
    },
]


@pytest.fixture
def repo(database_url):
    eng = create_engine(database_url)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    # The drop removes pg_search; a connection that saw it would go stale.
    eng.dispose()
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["url"] = database_url
    command.upgrade(config, "head")
    return Repo(eng)


def _mock_ia(hocr: bytes, page_numbers: bytes) -> None:
    respx.get(f"{BASE}/services/search/v1/scrape").respond(json={"items": ITEMS})
    for it in ITEMS:
        ident = it["identifier"]
        files = {f"{ident}_hocr.html": hocr, f"{ident}_page_numbers.json": page_numbers}
        respx.get(f"{BASE}/metadata/{ident}").respond(
            json={
                "server": "ia1.us.archive.org",
                "dir": f"/1/items/{ident}",
                "metadata": {},
                "files": [
                    {"name": n, "size": str(len(b)), "md5": hashlib.md5(b).hexdigest()}
                    for n, b in files.items()
                ],
            }
        )
        for n, b in files.items():
            respx.get(f"https://ia1.us.archive.org/1/items/{ident}/{n}").respond(content=b)


@respx.mock
async def test_every_stage(repo, tmp_path, hocr_bytes, page_numbers_bytes):
    _mock_ia(hocr_bytes, page_numbers_bytes)
    store = LocalStore(tmp_path)
    sel = S.Selection(PGJ)
    async with IAClient(BASE) as ia:
        assert (await S.enumerate_collection(repo, ia, PGJ)).done == 2
        assert S.classify_stage(repo, sel).done == 2
        assert S.lane_stage(repo, sel).done == 2
        assert (await S.mirror_stage(repo, ia, store, sel)).done == 2
    assert S.pages_stage(repo, store, sel).done == 2
    assert S.normalise_stage(repo, sel).done == 2
    assert S.passages_stage(repo, sel).done == 2

    issue = repo.item(ITEMS[0]["identifier"])
    other = repo.item(ITEMS[1]["identifier"])
    assert (issue["kind"], other["kind"]) == ("issue", "other")
    assert issue["license_lane"] == "public-domain" and issue["lane_status"] == "confirmed"
    assert other["license_lane"] == "public-domain" and other["lane_status"] == "to-verify"
    assert {f["name"] for f in issue["files"]} == {
        f"{ITEMS[0]['identifier']}_hocr.html",
        f"{ITEMS[0]['identifier']}_page_numbers.json",
    }

    with repo.engine.connect() as c:
        pages = c.execute(select(func.count()).select_from(db.page)).scalar_one()
        rows = (
            c.execute(
                select(db.passage, db.page.c.raw_text).join(
                    db.page,
                    (db.page.c.identifier == db.passage.c.identifier)
                    & (db.page.c.leaf == db.passage.c.leaf),
                )
            )
            .mappings()
            .all()
        )
    assert pages == 6  # three leaves in each item
    # only the issue is cut into passages, and not its duplicate leaf
    assert {r["identifier"] for r in rows} == {ITEMS[0]["identifier"]}
    assert {r["leaf"] for r in rows} == {0, 2}
    for r in rows:  # offsets point into raw text
        assert r["raw_text"][r["char_start"] : r["char_end"]].split()[0]

    # nothing is pending once done; a re-run is a no-op, including re-enumeration
    assert repo.pending("pages", PGJ) == []
    assert S.passages_stage(repo, sel).done == 0
    async with IAClient(BASE) as ia:
        again = await S.enumerate_collection(repo, ia, PGJ)
    assert (again.done, again.skipped) == (0, 2)
    assert repo.pending("classify", PGJ) == [] and repo.pending("index", PGJ) != []
    assert repo.pending("pages", PGJ, identifiers=[]) == []  # empty --ids selects nothing

    # re-laning an item withdraws its passages until they are rebuilt
    repo.mark(ITEMS[0]["identifier"], "lane", ok=True)
    with repo.engine.connect() as c:
        left = c.execute(select(func.count()).select_from(db.passage)).scalar_one()
    assert left == 0
    async with IAClient(BASE) as ia:
        assert (await S.mirror_stage(repo, ia, store, sel)).done == 1  # files already in lake
    for stage in (
        S.pages_stage(repo, store, sel),
        S.normalise_stage(repo, sel),
        S.passages_stage(repo, sel),
    ):
        assert stage.done == 1

    # finishing pages again invalidates everything downstream for that item
    repo.mark(ITEMS[0]["identifier"], "pages", ok=True)
    assert repo.pending("normalise", PGJ) == [ITEMS[0]["identifier"]]

    try:
        res = S.index_stage(repo, sel)
    except Exception as e:  # plain Postgres without pg_search
        if "pg_search" not in str(e) and "bm25" not in str(e):
            raise
        pytest.skip(f"pg_search not installed; BM25 step not exercised ({e})")
    else:
        assert res.done == 1  # the other item is waiting on normalise again
        # Re-marking pages withdrew the issue's passages; rebuild them to search.
        for stage in (S.normalise_stage(repo, sel), S.passages_stage(repo, sel)):
            assert stage.done == 1
        assert S.index_stage(repo, sel).done == 1
        with repo.engine.connect() as c:
            hits = c.execute(
                text("SELECT count(*) FROM passage WHERE search_text @@@ 'Martinsville'")
            ).scalar_one()
        assert hits >= 1


def test_items_outside_the_window_stop_after_lane_and_are_withdrawn(repo):
    repo.ensure_publication(PGJ, "P&GJ", 1)
    early = {
        **ITEMS[0],
        "identifier": "sim_pipeline-gas-journal_1879-01-02_30",
        "date": "1879-01-02",
    }
    late = {**ITEMS[0], "identifier": "sim_pipeline-gas-journal_1965_index", "date": "1965-01-01"}
    undated = {**ITEMS[0], "identifier": "sim_pipeline-gas-journal_undated"}
    del undated["date"]
    repo.upsert_items(PGJ, [ITEMS[0], early, late, undated])
    everyone = [
        ITEMS[0]["identifier"],
        early["identifier"],
        late["identifier"],
        "sim_pipeline-gas-journal_undated",
    ]
    for ident in everyone:
        for stage in ("enumerate", "classify", "lane"):
            repo.mark(ident, stage, ok=True)

    assert repo.pending("mirror", PGJ) == [ITEMS[0]["identifier"]]
    assert repo.outside_window(PGJ) == 3

    ident = early["identifier"]
    for stage in ("mirror", "pages", "normalise", "passages", "index"):
        repo.mark(ident, stage, ok=True)
    with repo.engine.begin() as c:
        c.execute(
            insert(db.passage).values(
                identifier=ident,
                leaf=0,
                seq=0,
                char_start=0,
                char_end=4,
                word_count=1,
                search_text="gas",
                collection=PGJ,
                kind="issue",
                license_lane="public-domain",
                pipeline_version=1,
            )
        )
    assert repo.withdraw_out_of_window() == [ident]
    with repo.engine.connect() as c:
        assert c.execute(select(func.count()).select_from(db.passage)).scalar_one() == 0
        stages = c.execute(
            select(db.stage_state.c.stage).where(db.stage_state.c.identifier == ident)
        ).scalars()
        assert set(stages) == {"enumerate", "classify", "lane"}
    assert repo.withdraw_out_of_window() == []  # nothing left to withdraw


def test_failures_are_recorded_and_retried(repo):
    repo.ensure_publication(PGJ, "P&GJ", 1)
    repo.upsert_items(PGJ, ITEMS[:1])
    ident = ITEMS[0]["identifier"]
    repo.mark(ident, "enumerate", ok=True)
    repo.mark(ident, "classify", ok=False, error="boom")
    assert repo.pending("classify", PGJ) == [ident]  # attempt 1 of 3
    repo.mark(ident, "classify", ok=False, error="boom")
    repo.mark(ident, "classify", ok=False, error="boom")
    assert repo.pending("classify", PGJ) == []  # gave up after 3
    assert repo.pending("classify", PGJ, force=True) == [ident]
    assert json.dumps(repo.failures()[0]["error"]) == '"boom"'
