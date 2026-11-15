"""The eight ingest stages. Each takes items in its predecessor's done state and records its own.

A failure is recorded against the item and never stops the run.
"""

import asyncio
import hashlib
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

import structlog
from corpus_core.classify import INDEXED_KINDS, Kind, classify
from corpus_core.collections import COLLECTIONS, EXCLUDED_COLLECTIONS
from corpus_core.hocr import Page, parse_hocr
from corpus_core.ia import MIRRORED_SUFFIXES, IAClient, IAError
from corpus_core.lanes import LaneStatus, LicenseLane, license_lanes
from corpus_core.normalise import search_text
from corpus_core.passages import Passage, passages
from corpus_core.storage import RawStore, raw_key

from corpus_ingest.repo import BY_NAME, Repo

log = structlog.get_logger()

REQUIRED_SUFFIXES = frozenset({"_hocr.html"})


@dataclass
class Result:
    stage: str
    done: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def ok(self) -> None:
        self.done += 1

    def fail(self, identifier: str, err: str) -> None:
        self.failed += 1
        if len(self.errors) < 10:
            self.errors.append(f"{identifier}: {err}")


@dataclass(frozen=True)
class Selection:
    collection: str | None = None
    identifiers: Sequence[str] | None = None
    limit: int | None = None
    force: bool = False


def _select(repo: Repo, stage: str, sel: Selection) -> list[str]:
    return repo.pending(stage, sel.collection, sel.identifiers, sel.limit, sel.force)


def _per_item(repo: Repo, stage: str, sel: Selection, fn: Callable[[str], None]) -> Result:
    res = Result(stage)
    for ident in _select(repo, stage, sel):
        try:
            fn(ident)
        except Exception as e:  # recorded per item; one bad item never stops the run
            repo.mark(ident, stage, ok=False, error=f"{type(e).__name__}: {e}")
            res.fail(ident, str(e))
            log.warning("stage_failed", stage=stage, identifier=ident, error=str(e))
        else:
            repo.mark(ident, stage, ok=True)
            res.ok()
    return res


# ---- 1. enumerate -------------------------------------------------------------------


async def enumerate_collection(
    repo: Repo, ia: IAClient, collection: str, identifiers: Sequence[str] | None = None
) -> Result:
    if collection in EXCLUDED_COLLECTIONS:
        raise ValueError(f"{collection} is excluded (Legend decision 0015)")
    spec = COLLECTIONS.get(collection)
    if spec is None:
        raise ValueError(f"unknown collection {collection}; add it to corpus_core.collections")
    repo.ensure_publication(collection, spec.name, spec.authority_tier)
    res = Result("enumerate")
    wanted = set(identifiers) if identifiers is not None else None
    batch: list[dict] = []

    def flush() -> None:
        # Only new or changed items are (re)marked: marking resets later stages, so unchanged
        # items keep their progress and a re-run of `run` really does skip finished work.
        changed = set(repo.upsert_items(collection, batch))
        never_marked = set(repo.without_stage([r["identifier"] for r in batch], "enumerate"))
        for ident in sorted(changed | never_marked):
            repo.mark(ident, "enumerate", ok=True)
            res.ok()
        res.skipped += len(batch) - len(changed | never_marked)
        batch.clear()

    async for row in ia.scrape(collection):
        if wanted is not None and row.get("identifier") not in wanted:
            continue
        batch.append(row)
        if len(batch) >= 500:
            flush()
    flush()
    return res


# ---- 2. classify, 3. lane -----------------------------------------------------------


def classify_stage(repo: Repo, sel: Selection) -> Result:
    def fn(ident: str) -> None:
        it = repo.item(ident)
        spec = COLLECTIONS.get(it["collection"])
        kind = classify(it["collection"], ident, it["title"])
        if spec and it["publisher"] and not spec.publisher_pattern.search(it["publisher"]):
            kind = Kind.OTHER
        repo.update_item(ident, kind=str(kind))

    return _per_item(repo, "classify", sel, fn)


def lane_stage(repo: Repo, sel: Selection) -> Result:
    def fn(ident: str) -> None:
        it = repo.item(ident)
        if it["issue_date"] is None:
            repo.update_item(
                ident,
                license_lane=str(LicenseLane.UNKNOWN),
                lane_status=str(LaneStatus.TO_VERIFY),
                license_basis="no publication date in the item metadata",
                retrieval_lane="ia-mirror",
            )
            return
        lanes = license_lanes(it["collection"], it["issue_date"])
        repo.update_item(
            ident,
            license_lane=str(lanes.license_lane),
            lane_status=str(lanes.lane_status),
            license_basis=lanes.license_basis,
            retrieval_lane=lanes.retrieval_lane,
        )

    return _per_item(repo, "lane", sel, fn)


# ---- 4. mirror ----------------------------------------------------------------------


async def mirror_stage(
    repo: Repo, ia: IAClient, store: RawStore, sel: Selection, concurrency: int = 8
) -> Result:
    res = Result("mirror")
    sem = asyncio.Semaphore(concurrency)

    async def one(ident: str) -> None:
        async with sem:
            try:
                it = repo.item(ident)
                meta = await ia.metadata(ident)
                if meta.restricted:
                    raise IAError("access-restricted item")
                kept = []
                for suffix in MIRRORED_SUFFIXES:
                    f = meta.file(suffix)
                    if f is None:
                        if suffix in REQUIRED_SUFFIXES:
                            raise IAError(f"no {suffix} file")
                        continue
                    key = raw_key(it["collection"], ident, f.name)
                    # Write-once: a stored file is never rewritten. Record what is actually in
                    # the lake, which differs from IA's current file if IA has since re-OCR'd.
                    body = None if store.exists(key) else await ia.download(meta, f)
                    if body is None or not store.put(key, body):
                        body = store.get(key)
                    kept.append(
                        {"name": f.name, "size": len(body), "md5": hashlib.md5(body).hexdigest()}
                    )
                repo.update_item(
                    ident,
                    server=meta.server,
                    dir=meta.dir,
                    files=kept,
                    restricted=str(meta.restricted).lower(),
                )
            except Exception as e:
                repo.mark(ident, "mirror", ok=False, error=f"{type(e).__name__}: {e}")
                res.fail(ident, str(e))
                log.warning("stage_failed", stage="mirror", identifier=ident, error=str(e))
            else:
                repo.mark(ident, "mirror", ok=True)
                res.ok()

    await asyncio.gather(*(one(i) for i in _select(repo, "mirror", sel)))
    return res


# ---- 5. pages -----------------------------------------------------------------------


def _parse(args: tuple[bytes, bytes | None]) -> list[Page]:
    return parse_hocr(*args)


def pages_stage(repo: Repo, store: RawStore, sel: Selection, workers: int = 1) -> Result:
    res = Result("pages")
    idents = _select(repo, "pages", sel)
    version = BY_NAME["pages"].version

    def load(ident: str) -> tuple[bytes, bytes | None]:
        it = repo.item(ident)
        hocr = store.get(raw_key(it["collection"], ident, f"{ident}_hocr.html"))
        pn_key = raw_key(it["collection"], ident, f"{ident}_page_numbers.json")
        return hocr, (store.get(pn_key) if store.exists(pn_key) else None)

    def record(ident: str, pages: list[Page] | Exception) -> None:
        if isinstance(pages, Exception):
            repo.mark(ident, "pages", ok=False, error=f"{type(pages).__name__}: {pages}")
            res.fail(ident, str(pages))
            return
        repo.replace_pages_and_mark(ident, pages, version)
        res.ok()

    if workers <= 1:
        for ident in idents:
            try:
                record(ident, _parse(load(ident)))
            except Exception as e:
                record(ident, e)
        return res

    # Bounded batches: hOCR files run to tens of MB, so never hold more than 2x workers in memory.
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(idents), 2 * workers):
            futures = {}
            for ident in idents[start : start + 2 * workers]:
                try:
                    futures[ident] = pool.submit(_parse, load(ident))
                except Exception as e:
                    record(ident, e)
            for ident, fut in futures.items():
                try:
                    record(ident, fut.result())
                except Exception as e:
                    record(ident, e)
    return res


# ---- 6. normalise, 7. passages ------------------------------------------------------


def normalise_stage(repo: Repo, sel: Selection) -> Result:
    def fn(ident: str) -> None:
        texts = {
            p["leaf"]: search_text(p["raw_text"], drop_running_head=True) for p in repo.pages(ident)
        }
        repo.set_search_texts(ident, texts)

    return _per_item(repo, "normalise", sel, fn)


def passages_stage(repo: Repo, sel: Selection) -> Result:
    version = BY_NAME["passages"].version

    def fn(ident: str) -> None:
        it = repo.item(ident)
        by_leaf: dict[int, list[Passage]] = {}
        if it["kind"] in INDEXED_KINDS:
            for p in repo.pages(ident):
                if p["duplicate_of_leaf"] is None:
                    by_leaf[p["leaf"]] = passages(p["raw_text"])
        repo.replace_passages(ident, by_leaf, it, version)

    return _per_item(repo, "passages", sel, fn)


# ---- 8. index -----------------------------------------------------------------------


def index_stage(repo: Repo, sel: Selection) -> Result:
    """pg_search keeps the BM25 index current on insert; this stage makes sure it exists and
    records which items are searchable."""
    repo.ensure_bm25()
    return _per_item(repo, "index", sel, lambda _ident: None)
