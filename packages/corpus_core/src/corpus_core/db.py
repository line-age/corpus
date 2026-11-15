"""The ingest schema, in the ingest database (corpus-ingest-db).

The tables are created by the repo's single Alembic history (migrations/ at the repo root,
revision 0002 onwards). Change them there with a new revision, not here alone.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

metadata = MetaData()

publication = Table(
    "publication",
    metadata,
    Column("collection", String, primary_key=True),
    Column("name", Text, nullable=False),
    Column("authority_tier", SmallInteger, nullable=False),
)

item = Table(
    "item",
    metadata,
    Column("identifier", String, primary_key=True),
    Column("collection", String, ForeignKey("publication.collection"), nullable=False),
    Column("title", Text, nullable=False),
    Column("issue_date", Date),
    Column("volume", String),
    Column("issue", String),
    Column("publisher", Text),
    Column("imagecount", Integer),
    Column("kind", String),
    Column("license_lane", String),
    Column("lane_status", String),
    Column("license_basis", Text),
    Column("retrieval_lane", String),
    Column("server", String),
    Column("dir", String),
    Column("files", JSONB),  # mirrored files: name, size, md5
    Column("restricted", String),
    Column("enumerated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Index("item_collection_date", "collection", "issue_date"),
)

stage_state = Table(
    "stage_state",
    metadata,
    Column("identifier", String, ForeignKey("item.identifier", ondelete="CASCADE")),
    Column("stage", String),
    Column("status", String, nullable=False),  # done | failed
    Column("pipeline_version", Integer, nullable=False),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("error", Text),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    PrimaryKeyConstraint("identifier", "stage"),
    Index("stage_state_stage_status", "stage", "status"),
)

page = Table(
    "page",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("identifier", String, ForeignKey("item.identifier", ondelete="CASCADE"), nullable=False),
    Column("leaf", Integer, nullable=False),
    Column("printed_page", String),
    Column("raw_text", Text, nullable=False),
    Column("search_text", Text),
    Column("word_count", Integer, nullable=False),
    Column("mean_conf", Float),
    Column("text_hash", String(64), nullable=False),
    Column("duplicate_of_leaf", Integer),
    Column("pipeline_version", Integer, nullable=False),
    UniqueConstraint("identifier", "leaf"),
)

passage = Table(
    "passage",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("identifier", String, ForeignKey("item.identifier", ondelete="CASCADE"), nullable=False),
    Column("leaf", Integer, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("char_start", Integer, nullable=False),
    Column("char_end", Integer, nullable=False),
    Column("word_count", Integer, nullable=False),
    Column("search_text", Text, nullable=False),
    # denormalised from item so search can filter without a join
    Column("collection", String, nullable=False),
    Column("issue_date", Date),
    Column("kind", String, nullable=False),
    Column("license_lane", String, nullable=False),
    Column("pipeline_version", Integer, nullable=False),
    UniqueConstraint("identifier", "leaf", "seq"),
    Index("passage_filters", "collection", "kind", "license_lane", "issue_date"),
)

BM25_INDEX = "passage_bm25"

BM25_DDL = f"""
CREATE INDEX IF NOT EXISTS {BM25_INDEX} ON passage
USING bm25 (id, search_text, issue_date)
WITH (key_field = 'id')
"""


def engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, future=True)
