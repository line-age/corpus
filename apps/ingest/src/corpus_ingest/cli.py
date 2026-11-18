"""corpus-ingest: one command per stage, plus run, status and export."""

import asyncio
import csv
import os
import sys
from pathlib import Path
from typing import Annotated

import structlog
import typer
from corpus_core.collections import FIRST_YEAR, LAST_YEAR
from corpus_core.config import get_settings
from corpus_core.db import engine
from corpus_core.ia import IAClient
from corpus_core.storage import open_store

from corpus_ingest import stages as S
from corpus_ingest.repo import STAGES, Repo

app = typer.Typer(
    help="Mirror the trade press from Internet Archive into a searchable index."
)

Collection = Annotated[
    str | None, typer.Option("--collection", "-c", help="IA collection id")
]
Ids = Annotated[
    Path | None, typer.Option("--ids", help="file of identifiers, one per line")
]
Limit = Annotated[
    int | None, typer.Option("--limit", "-n", help="at most this many items")
]
Force = Annotated[bool, typer.Option("--force", help="redo items already done")]


def _repo() -> Repo:
    s = get_settings()
    return Repo(engine(s.database_url), max_attempts=s.max_attempts)


def _ia() -> IAClient:
    s = get_settings()
    return IAClient(s.ia_base_url, s.user_agent, s.http_timeout_s, s.max_attempts)


def _store():
    s = get_settings()
    return open_store(s.raw_store, s.s3_endpoint_url)


def _ids(path: Path | None) -> list[str] | None:
    if path is None:
        return None
    lines = (ln.split("#", 1)[0].strip() for ln in path.read_text().splitlines())
    return [ln for ln in lines if ln]


def _sel(
    collection: str | None, ids: Path | None, limit: int | None, force: bool
) -> S.Selection:
    return S.Selection(collection, _ids(ids), limit, force)


def _report(res: S.Result) -> None:
    skipped = f", {res.skipped} unchanged" if res.skipped else ""
    typer.echo(f"{res.stage}: {res.done} done, {res.failed} failed{skipped}")
    for e in res.errors:
        typer.echo(f"  failed {e}", err=True)


@app.callback()
def _setup() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


@app.command("enumerate")
def enumerate_cmd(
    collection: Annotated[str, typer.Option("--collection", "-c")], ids: Ids = None
) -> None:
    """List every item in a collection (or only those in --ids) into `item`."""

    async def go() -> S.Result:
        async with _ia() as ia:
            return await S.enumerate_collection(_repo(), ia, collection, _ids(ids))

    _report(asyncio.run(go()))


@app.command()
def classify(
    collection: Collection = None,
    ids: Ids = None,
    limit: Limit = None,
    force: Force = False,
) -> None:
    """Record each item's kind: issue, index, supplement or other."""
    _report(S.classify_stage(_repo(), _sel(collection, ids, limit, force)))


@app.command()
def lane(
    collection: Collection = None,
    ids: Ids = None,
    limit: Limit = None,
    force: Force = False,
) -> None:
    """Record license and retrieval lanes (controlplane I-0006)."""
    _report(S.lane_stage(_repo(), _sel(collection, ids, limit, force)))


@app.command()
def mirror(
    collection: Collection = None,
    ids: Ids = None,
    limit: Limit = None,
    force: Force = False,
) -> None:
    """Copy each item's hOCR, page numbers, text and meta files into the raw lake."""

    async def go() -> S.Result:
        async with _ia() as ia:
            return await S.mirror_stage(
                _repo(),
                ia,
                _store(),
                _sel(collection, ids, limit, force),
                get_settings().concurrency,
            )

    _report(asyncio.run(go()))


@app.command()
def pages(
    collection: Collection = None,
    ids: Ids = None,
    limit: Limit = None,
    force: Force = False,
    workers: Annotated[int, typer.Option(help="parser processes")] = max(
        1, (os.cpu_count() or 2) - 1
    ),
) -> None:
    """Split each mirrored item into one row per leaf."""
    _report(
        S.pages_stage(_repo(), _store(), _sel(collection, ids, limit, force), workers)
    )


@app.command()
def normalise(
    collection: Collection = None,
    ids: Ids = None,
    limit: Limit = None,
    force: Force = False,
) -> None:
    """Derive each page's search text. Raw text is untouched."""
    _report(S.normalise_stage(_repo(), _sel(collection, ids, limit, force)))


@app.command("passages")
def passages_cmd(
    collection: Collection = None,
    ids: Ids = None,
    limit: Limit = None,
    force: Force = False,
) -> None:
    """Cut indexed kinds into overlapping passages with offsets into raw text."""
    _report(S.passages_stage(_repo(), _sel(collection, ids, limit, force)))


@app.command()
def index(
    collection: Collection = None,
    ids: Ids = None,
    limit: Limit = None,
    force: Force = False,
) -> None:
    """Make sure the BM25 index exists and record items as searchable."""
    _report(S.index_stage(_repo(), _sel(collection, ids, limit, force)))


@app.command()
def run(
    collection: Annotated[str, typer.Option("--collection", "-c")],
    ids: Ids = None,
    limit: Limit = None,
    skip_enumerate: Annotated[
        bool, typer.Option(help="items are already listed")
    ] = False,
) -> None:
    """Every stage in order for one collection. Safe to re-run; done items are skipped."""
    ident_list = _ids(ids)
    sel = S.Selection(collection, ident_list, limit, False)
    repo, store, cfg = _repo(), _store(), get_settings()

    async def network() -> list[S.Result]:
        out = []
        async with _ia() as ia:
            if not skip_enumerate:
                out.append(
                    await S.enumerate_collection(repo, ia, collection, ident_list)
                )
            out.append(S.classify_stage(repo, sel))
            out.append(S.lane_stage(repo, sel))
            out.append(await S.mirror_stage(repo, ia, store, sel, cfg.concurrency))
        return out

    results = asyncio.run(network())
    results.append(S.pages_stage(repo, store, sel, max(1, (os.cpu_count() or 2) - 1)))
    results.append(S.normalise_stage(repo, sel))
    results.append(S.passages_stage(repo, sel))
    results.append(S.index_stage(repo, sel))
    for r in results:
        _report(r)


@app.command()
def status(collection: Collection = None) -> None:
    """Items per stage and state, and the latest failures."""
    repo = _repo()
    counts = {(r["stage"], r["status"]): r["items"] for r in repo.status(collection)}
    typer.echo(f"{'stage':<10} {'done':>8} {'failed':>8}")
    for st in STAGES:
        done, failed = (
            counts.get((st.name, "done"), 0),
            counts.get((st.name, "failed"), 0),
        )
        typer.echo(f"{st.name:<10} {done:>8} {failed:>8}")
    outside = repo.outside_window(collection)
    if outside:
        typer.echo(
            f"\n{outside} listed items are outside {FIRST_YEAR}-{LAST_YEAR}; they stop after lane."
        )
    fails = repo.failures()
    if fails:
        typer.echo("\nlatest failures:")
        for f in fails:
            typer.echo(f"  {f['stage']:<10} {f['identifier']}  {f['error']}")


@app.command()
def export(
    out: Annotated[Path, typer.Option("--out", "-o")] = Path("sample-review.csv"),
    sample: Annotated[int, typer.Option("--sample")] = 20,
    collection: Collection = None,
) -> None:
    """A CSV of random pages with blank review columns, for a person to score."""
    rows = _repo().sample_pages(sample, collection)
    fields = [
        "identifier",
        "title",
        "issue_date",
        "kind",
        "leaf",
        "printed_page",
        "page_url",
        "word_count",
        "mean_conf",
        "duplicate_of_leaf",
        "license_lane",
        "raw_text",
        "search_text",
        "review_legible_1to5",
        "review_ocr_matches_scan_1to5",
        "review_notes",
    ]
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    **r,
                    "page_url": f"https://archive.org/details/{r['identifier']}/page/n{r['leaf']}/mode/1up",
                    "raw_text": (r["raw_text"] or "")[:1500],
                    "search_text": (r["search_text"] or "")[:1500],
                }
            )
    typer.echo(f"wrote {len(rows)} pages to {out}")


if __name__ == "__main__":
    app()
