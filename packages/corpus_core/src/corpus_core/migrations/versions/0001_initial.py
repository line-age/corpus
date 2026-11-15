"""initial schema: publication, item, stage_state, page, passage

Revision ID: 0001
Revises:
"""

from alembic import op
from corpus_core.db import metadata

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = ("publication", "item", "stage_state", "page", "passage")


def upgrade() -> None:
    bind = op.get_bind()
    # Frozen at this revision's table set; later changes get their own revisions.
    metadata.create_all(bind, tables=[metadata.tables[t] for t in TABLES])


def downgrade() -> None:
    bind = op.get_bind()
    metadata.drop_all(bind, tables=[metadata.tables[t] for t in reversed(TABLES)])
