"""Ingest schema: publication, item, stage_state, page, passage.

The BM25 index needs pg_search, so the ingest `index` stage creates it, not this revision.
Revision 0004 now creates it too.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "publication",
        sa.Column("collection", sa.String(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("authority_tier", sa.SmallInteger(), nullable=False),
        sa.PrimaryKeyConstraint("collection"),
    )
    op.create_table(
        "item",
        sa.Column("identifier", sa.String(), nullable=False),
        sa.Column("collection", sa.String(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("volume", sa.String(), nullable=True),
        sa.Column("issue", sa.String(), nullable=True),
        sa.Column("publisher", sa.Text(), nullable=True),
        sa.Column("imagecount", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(), nullable=True),
        sa.Column("license_lane", sa.String(), nullable=True),
        sa.Column("lane_status", sa.String(), nullable=True),
        sa.Column("license_basis", sa.Text(), nullable=True),
        sa.Column("retrieval_lane", sa.String(), nullable=True),
        sa.Column("server", sa.String(), nullable=True),
        sa.Column("dir", sa.String(), nullable=True),
        sa.Column("files", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("restricted", sa.String(), nullable=True),
        sa.Column(
            "enumerated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["collection"],
            ["publication.collection"],
        ),
        sa.PrimaryKeyConstraint("identifier"),
    )
    op.create_index("item_collection_date", "item", ["collection", "issue_date"], unique=False)
    op.create_table(
        "stage_state",
        sa.Column("identifier", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("pipeline_version", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["identifier"], ["item.identifier"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("identifier", "stage"),
    )
    op.create_index("stage_state_stage_status", "stage_state", ["stage", "status"], unique=False)
    op.create_table(
        "page",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("identifier", sa.String(), nullable=False),
        sa.Column("leaf", sa.Integer(), nullable=False),
        sa.Column("printed_page", sa.String(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("mean_conf", sa.Float(), nullable=True),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("duplicate_of_leaf", sa.Integer(), nullable=True),
        sa.Column("pipeline_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["identifier"], ["item.identifier"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identifier", "leaf"),
    )
    op.create_table(
        "passage",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("identifier", sa.String(), nullable=False),
        sa.Column("leaf", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("collection", sa.String(), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("license_lane", sa.String(), nullable=False),
        sa.Column("pipeline_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["identifier"], ["item.identifier"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identifier", "leaf", "seq"),
    )
    op.create_index(
        "passage_filters",
        "passage",
        ["collection", "kind", "license_lane", "issue_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("passage")
    op.drop_table("page")
    op.drop_table("stage_state")
    op.drop_table("item")
    op.drop_table("publication")
