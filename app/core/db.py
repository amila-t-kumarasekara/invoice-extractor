import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.core.config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class DocumentStatus(str, enum.Enum):
    """uploaded -> parsed -> [classified] -> extracted -> validated ->
    approved | needs_review | failed | rejected

    `classified` is defined now so the schema doesn't need another migration
    when Phase 5 (classify/route) lands, but nothing sets it yet - every
    document is currently assumed to be an invoice.
    """

    uploaded = "uploaded"
    parsed = "parsed"
    classified = "classified"
    extracted = "extracted"
    validated = "validated"
    approved = "approved"
    needs_review = "needs_review"
    failed = "failed"
    rejected = "rejected"  # blocked by a deterministic security check, never retried


class JobStage(str, enum.Enum):
    parse = "parse"
    extract = "extract"
    validate = "validate"


class JobStatus(str, enum.Enum):
    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("tenant_id", "file_hash", name="uq_document_tenant_hash"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    file_hash: Mapped[str] = mapped_column(String, nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False, default="application/pdf")
    storage_key: Mapped[str] = mapped_column(String, nullable=False)
    doc_type: Mapped[str] = mapped_column(String, nullable=False, default="invoice")
    status: Mapped[str] = mapped_column(String, nullable=False, default=DocumentStatus.uploaded.value)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    pages: Mapped[list["Page"]] = relationship(back_populates="document", cascade="all, delete-orphan", order_by="Page.page_no")
    jobs: Mapped[list["Job"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    extractions: Mapped[list["Extraction"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Extraction.attempt_no"
    )


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    text_source: Mapped[str] = mapped_column(String, nullable=False)  # "text_layer" | "ocr"
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Populated in Phase 4 (rendered page image) / Phase 6 (word boxes for grounding).
    image_key: Mapped[str | None] = mapped_column(String, nullable=True)
    words: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped["Document"] = relationship(back_populates="pages")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default=JobStatus.queued.value)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    document: Mapped["Document"] = relationship(back_populates="jobs")


class Extraction(Base):
    __tablename__ = "extractions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String, nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    tokens: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # {"input": int, "output": int}
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped["Document"] = relationship(back_populates="extractions")
    field_values: Mapped[list["FieldValue"]] = relationship(back_populates="extraction", cascade="all, delete-orphan")
    issues: Mapped[list["ValidationIssue"]] = relationship(back_populates="extraction", cascade="all, delete-orphan")


class FieldValue(Base):
    """One row per extracted field. `page_no`/`bbox`/`source_text` stay null
    until Phase 6 (grounding) computes them; `field`/`value` are populated
    today so per-field data is queryable even before grounding exists."""

    __tablename__ = "field_values"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    extraction_id: Mapped[str] = mapped_column(ForeignKey("extractions.id"), nullable=False, index=True)
    field: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=True)
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    extraction: Mapped["Extraction"] = relationship(back_populates="field_values")


class ValidationIssue(Base):
    __tablename__ = "validation_issues"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    extraction_id: Mapped[str] = mapped_column(ForeignKey("extractions.id"), nullable=False, index=True)
    field: Mapped[str | None] = mapped_column(String, nullable=True)
    rule: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False, default="error")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    extraction: Mapped["Extraction"] = relationship(back_populates="issues")


class Correction(Base):
    """Unused until Phase 10 (review UI) - table exists now so the migration
    doesn't need to change shape later."""

    __tablename__ = "corrections"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    field_value_id: Mapped[str] = mapped_column(ForeignKey("field_values.id"), nullable=False, index=True)
    old_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_sessionmaker():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def get_db():
    """FastAPI dependency: yields a session, commits on success, rolls back on error."""
    Session = get_sessionmaker()
    db = Session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
