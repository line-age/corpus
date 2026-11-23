"""Search on pg_search: create the extension and the BM25 index over passages.

The index matches corpus_core.db.BM25_DDL, which the ingest `index` stage also runs, so
both create the same index and whichever runs second does nothing. Its first column is
the key; pg_search 0.26 and later no longer take a key_field option.

pg_search requires the vector extension, so CASCADE installs it where 0001 has not.
Nothing in corpus uses vector itself; it stays only because pg_search depends on it.

Downgrade does nothing. pg_search and the index stay, because the ingest `index` stage
created them before this revision existed.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_search CASCADE")
    op.execute(
        "CREATE INDEX IF NOT EXISTS passage_bm25 ON passage "
        "USING bm25 (id, search_text, issue_date)"
    )


def downgrade() -> None:
    pass
