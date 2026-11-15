"""Database access for ingest. Stage bookkeeping lives in `stage_state`."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from corpus_core import db
from corpus_core.collections import WINDOW_END, WINDOW_START
from corpus_core.hocr import Page
from corpus_core.passages import Passage
from sqlalchemy import and_, delete, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine


@dataclass(frozen=True)
class Stage:
    name: str
    prev: str | None
    version: int


# Bump a stage's version when its output changes: items done at an older version re-run.
STAGES: tuple[Stage, ...] = (
    Stage("enumerate", None, 1),
    Stage("classify", "enumerate", 1),
    Stage("lane", "classify", 1),
    Stage("mirror", "lane", 1),
    Stage("pages", "mirror", 1),
    Stage("normalise", "pages", 1),
    Stage("passages", "normalise", 1),
    Stage("index", "passages", 1),
)
BY_NAME = {s.name: s for s in STAGES}
_NAMES = [s.name for s in STAGES]
WINDOWED_STAGES = tuple(_NAMES[_NAMES.index("mirror") :])


def downstream(stage: str) -> list[str]:
    return _NAMES[_NAMES.index(stage) + 1 :]


def _clears_passages(stage: str, ok: bool) -> bool:
    """Passages copy kind and lanes from the item, so they are withdrawn when anything before
    them changes or they fail."""
    i, p = _NAMES.index(stage), _NAMES.index("passages")
    return i < p or (i == p and not ok)


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    s = str(value)[:10]
    for n in (10, 7, 4):
        try:
            parts = [int(x) for x in s[:n].split("-")]
            return date(
                parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1
            )
        except (ValueError, IndexError):
            continue
    return None


class Repo:
    def __init__(self, engine: Engine, max_attempts: int = 3) -> None:
        self.engine = engine
        self.max_attempts = max_attempts

    def ensure_publication(self, collection: str, name: str, tier: int) -> None:
        stmt = pg_insert(db.publication).values(
            collection=collection, name=name, authority_tier=tier
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["collection"], set_={"name": name, "authority_tier": tier}
        )
        with self.engine.begin() as c:
            c.execute(stmt)

    def upsert_items(self, collection: str, rows: Iterable[dict[str, Any]]) -> list[str]:
        """Insert new items and update changed ones; returns the identifiers inserted or changed.
        Unchanged rows are left alone so their progress survives a re-enumeration."""
        vals = [
            {
                "identifier": r["identifier"],
                "collection": collection,
                "title": str(r.get("title") or ""),
                "issue_date": parse_date(r.get("date")),
                "volume": _s(r.get("volume")),
                "issue": _s(r.get("issue")),
                "publisher": _s(r.get("publisher")),
                "imagecount": _i(r.get("imagecount")),
            }
            for r in rows
        ]
        if not vals:
            return []
        stmt = pg_insert(db.item).values(vals)
        cols = ("title", "issue_date", "volume", "issue", "publisher", "imagecount")
        stmt = stmt.on_conflict_do_update(
            index_elements=["identifier"],
            set_={c: stmt.excluded[c] for c in cols},
            where=or_(*(db.item.c[c].is_distinct_from(stmt.excluded[c]) for c in cols)),
        ).returning(db.item.c.identifier)
        with self.engine.begin() as c:
            return list(c.execute(stmt).scalars())

    def without_stage(self, identifiers: Sequence[str], stage: str) -> list[str]:
        if not identifiers:
            return []
        q = select(db.item.c.identifier).where(
            db.item.c.identifier.in_(list(identifiers)),
            ~select(db.stage_state.c.identifier)
            .where(
                db.stage_state.c.identifier == db.item.c.identifier,
                db.stage_state.c.stage == stage,
            )
            .exists(),
        )
        with self.engine.connect() as c:
            return list(c.execute(q).scalars())

    def item(self, identifier: str) -> dict[str, Any]:
        with self.engine.connect() as c:
            row = (
                c.execute(select(db.item).where(db.item.c.identifier == identifier))
                .mappings()
                .one()
            )
        return dict(row)

    def update_item(self, identifier: str, **values: Any) -> None:
        with self.engine.begin() as c:
            c.execute(update(db.item).where(db.item.c.identifier == identifier).values(**values))

    def pending(
        self,
        stage: str,
        collection: str | None = None,
        identifiers: Sequence[str] | None = None,
        limit: int | None = None,
        force: bool = False,
    ) -> list[str]:
        st = BY_NAME[stage]
        cur = db.stage_state.alias("cur")
        q = select(db.item.c.identifier).select_from(db.item)
        if st.prev is not None:
            prev = db.stage_state.alias("prev")
            q = q.join(
                prev,
                and_(
                    prev.c.identifier == db.item.c.identifier,
                    prev.c.stage == st.prev,
                    prev.c.status == "done",
                ),
            )
        q = q.outerjoin(cur, and_(cur.c.identifier == db.item.c.identifier, cur.c.stage == stage))
        if not force:
            q = q.where(
                or_(
                    cur.c.identifier.is_(None),
                    cur.c.pipeline_version < st.version,
                    and_(cur.c.status == "failed", cur.c.attempts < self.max_attempts),
                )
            )
        if stage in WINDOWED_STAGES:
            q = q.where(_in_window(db.item.c.issue_date))
        if collection:
            q = q.where(db.item.c.collection == collection)
        if identifiers is not None:
            q = q.where(db.item.c.identifier.in_(list(identifiers)))
        q = q.order_by(db.item.c.identifier)
        if limit:
            q = q.limit(limit)
        with self.engine.connect() as c:
            return list(c.execute(q).scalars())

    def mark(
        self,
        identifier: str,
        stage: str,
        ok: bool,
        error: str | None = None,
        conn: Connection | None = None,
    ) -> None:
        if conn is None:
            with self.engine.begin() as c:
                self._mark(c, identifier, stage, ok, error)
        else:
            self._mark(conn, identifier, stage, ok, error)

    def _mark(
        self, c: Connection, identifier: str, stage: str, ok: bool, error: str | None
    ) -> None:
        st = BY_NAME[stage]
        stmt = pg_insert(db.stage_state).values(
            identifier=identifier,
            stage=stage,
            status="done" if ok else "failed",
            pipeline_version=st.version,
            attempts=0 if ok else 1,
            error=None if ok else (error or "")[:2000],
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["identifier", "stage"],
            set_={
                "status": stmt.excluded.status,
                "pipeline_version": stmt.excluded.pipeline_version,
                "attempts": 0 if ok else db.stage_state.c.attempts + 1,
                "error": stmt.excluded.error,
                "updated_at": func.now(),
            },
        )
        c.execute(stmt)
        if ok:
            c.execute(
                delete(db.stage_state).where(
                    db.stage_state.c.identifier == identifier,
                    db.stage_state.c.stage.in_(downstream(stage)),
                )
            )
        if _clears_passages(stage, ok):
            c.execute(delete(db.passage).where(db.passage.c.identifier == identifier))

    def replace_pages_and_mark(self, identifier: str, pages: Sequence[Page], version: int) -> None:
        with self.engine.begin() as c:
            c.execute(delete(db.passage).where(db.passage.c.identifier == identifier))
            c.execute(delete(db.page).where(db.page.c.identifier == identifier))
            if pages:
                c.execute(
                    insert(db.page),
                    [
                        {
                            "identifier": identifier,
                            "leaf": p.leaf,
                            "printed_page": p.printed_page,
                            "raw_text": p.raw_text,
                            "word_count": p.word_count,
                            "mean_conf": p.mean_conf,
                            "text_hash": p.text_hash,
                            "duplicate_of_leaf": p.duplicate_of_leaf,
                            "pipeline_version": version,
                        }
                        for p in pages
                    ],
                )
            self._mark(c, identifier, "pages", True, None)

    def pages(self, identifier: str) -> list[dict[str, Any]]:
        q = select(db.page).where(db.page.c.identifier == identifier).order_by(db.page.c.leaf)
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(q).mappings()]

    def set_search_texts(self, identifier: str, texts: dict[int, str]) -> None:
        with self.engine.begin() as c:
            for leaf, t in texts.items():
                c.execute(
                    update(db.page)
                    .where(db.page.c.identifier == identifier, db.page.c.leaf == leaf)
                    .values(search_text=t)
                )

    def replace_passages(
        self,
        identifier: str,
        by_leaf: Mapping[int, Sequence[Passage]],
        item: dict[str, Any],
        version: int,
    ) -> int:
        rows = [
            {
                "identifier": identifier,
                "leaf": leaf,
                "seq": p.seq,
                "char_start": p.char_start,
                "char_end": p.char_end,
                "word_count": p.word_count,
                "search_text": p.search_text,
                "collection": item["collection"],
                "issue_date": item["issue_date"],
                "kind": item["kind"],
                "license_lane": item["license_lane"],
                "pipeline_version": version,
            }
            for leaf, ps in by_leaf.items()
            for p in ps
        ]
        with self.engine.begin() as c:
            c.execute(delete(db.passage).where(db.passage.c.identifier == identifier))
            if rows:
                c.execute(insert(db.passage), rows)
        return len(rows)

    def ensure_bm25(self) -> None:
        with self.engine.begin() as c:
            has = c.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'pg_search'")).first()
            if not has:
                c.execute(text("CREATE EXTENSION pg_search"))
            c.execute(text(db.BM25_DDL))

    def withdraw_out_of_window(self) -> list[str]:
        """Delete pages, passages and progress from mirror onwards for items outside the corpus
        window. Their raw files stay in the lake."""
        outside = (
            select(db.item.c.identifier)
            .join(db.stage_state, db.stage_state.c.identifier == db.item.c.identifier)
            .where(
                db.stage_state.c.stage.in_(WINDOWED_STAGES),
                ~_in_window(db.item.c.issue_date),
            )
            .distinct()
        )
        with self.engine.begin() as c:
            idents = sorted(c.execute(outside).scalars())
            if idents:
                c.execute(delete(db.passage).where(db.passage.c.identifier.in_(idents)))
                c.execute(delete(db.page).where(db.page.c.identifier.in_(idents)))
                c.execute(
                    delete(db.stage_state).where(
                        db.stage_state.c.identifier.in_(idents),
                        db.stage_state.c.stage.in_(WINDOWED_STAGES),
                    )
                )
        return idents

    def outside_window(self, collection: str | None = None) -> int:
        q = select(func.count()).select_from(db.item).where(~_in_window(db.item.c.issue_date))
        if collection:
            q = q.where(db.item.c.collection == collection)
        with self.engine.connect() as c:
            return c.execute(q).scalar_one()

    def status(self, collection: str | None = None) -> list[dict[str, Any]]:
        q = (
            select(db.stage_state.c.stage, db.stage_state.c.status, func.count().label("items"))
            .join(db.item, db.item.c.identifier == db.stage_state.c.identifier)
            .group_by(db.stage_state.c.stage, db.stage_state.c.status)
        )
        if collection:
            q = q.where(db.item.c.collection == collection)
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(q).mappings()]

    def failures(self, limit: int = 20) -> list[dict[str, Any]]:
        q = (
            select(db.stage_state)
            .where(db.stage_state.c.status == "failed")
            .order_by(db.stage_state.c.updated_at.desc())
            .limit(limit)
        )
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(q).mappings()]

    def sample_pages(self, n: int, collection: str | None = None) -> list[dict[str, Any]]:
        q = (
            select(
                db.page.c.identifier,
                db.page.c.leaf,
                db.page.c.printed_page,
                db.page.c.word_count,
                db.page.c.mean_conf,
                db.page.c.duplicate_of_leaf,
                db.page.c.raw_text,
                db.page.c.search_text,
                db.item.c.title,
                db.item.c.issue_date,
                db.item.c.kind,
                db.item.c.license_lane,
            )
            .join(db.item, db.item.c.identifier == db.page.c.identifier)
            .where(db.page.c.word_count > 0)
            .order_by(func.random())
            .limit(n)
        )
        if collection:
            q = q.where(db.item.c.collection == collection)
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(q).mappings()]


def _s(v: Any) -> str | None:
    return None if v in (None, "") else str(v)


def _i(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _in_window(issue_date: Any) -> Any:
    return and_(issue_date.is_not(None), issue_date.between(WINDOW_START, WINDOW_END))
