"""Drop documents and pages, the store of the retired `make ingest`.

Search reads the ingest tables (item, page, passage) instead. pg_trgm served only the
trigram index on pages.text, so it goes too. Downgrade recreates all three as 0003 did,
empty.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_pages_text_trgm", table_name="pages")
    op.drop_table("pages")
    op.drop_table("documents")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")


def downgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("identifier", sa.String(length=255), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("volume", sa.String(length=50), nullable=True),
        sa.Column("issue", sa.String(length=50), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identifier"),
    )
    op.create_table(
        "pages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("leaf_number", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.String(length=20), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "leaf_number", name="uq_pages_document_id_leaf_number"
        ),
    )
    op.create_index(
        "ix_pages_text_trgm",
        "pages",
        ["text"],
        postgresql_using="gin",
        postgresql_ops={"text": "gin_trgm_ops"},
    )
