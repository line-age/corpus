"""ORM models for the ingested corpus, shared by ingest (which writes them) and the API.

A `Document` is one scanned publication, such as one issue of a journal. Its OCR text is
stored one row per scanned page in `Page`, so a search hit can cite the page it came from.

New model modules must also be imported in `migrations/env.py`, or Alembic autogenerate
will not see them.
"""

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from corpus.db import Base


class Document(Base):
    """One scanned publication, such as one issue of a journal."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    identifier: Mapped[str] = mapped_column(String(255), unique=True)
    """The source's stable ID for the item, e.g. an archive.org identifier."""
    title: Mapped[str] = mapped_column(Text)
    publication_date: Mapped[date | None] = mapped_column(Date)
    volume: Mapped[str | None] = mapped_column(String(50))
    issue: Mapped[str | None] = mapped_column(String(50))
    source_url: Mapped[str] = mapped_column(Text)
    """Where a reader can view the original scan. Page links are built from it."""
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    pages: Mapped[list["Page"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Page.leaf_number",
    )


class Page(Base):
    """The OCR text of one scanned page of a document."""

    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "leaf_number", name="uq_pages_document_id_leaf_number"
        ),
        # Trigram index so case-insensitive substring filters (ILIKE '%term%') can use an
        # index instead of scanning every page. Needs the pg_trgm extension.
        Index(
            "ix_pages_text_trgm",
            "text",
            postgresql_using="gin",
            postgresql_ops={"text": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE")
    )
    """Its document. The unique constraint on (document_id, leaf_number) indexes it."""
    leaf_number: Mapped[int] = mapped_column(Integer)
    """0-based position of the page in the scan, counting covers and unnumbered pages."""
    page_number: Mapped[str | None] = mapped_column(String(20))
    """The page number printed on the page, when the source detected one."""
    text: Mapped[str] = mapped_column(Text)

    document: Mapped[Document] = relationship(back_populates="pages")
