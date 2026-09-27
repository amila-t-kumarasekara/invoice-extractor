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
    """uploaded -> parsed -> classified -> extracted -> validated ->
    approved | needs_review | failed | rejected

    If an upload actually contained more than one document type
    (page-splitting, see ClassifyStage), the primary document just continues
    through this same state machine for its own page group - there's no
    separate "split" status. Its relationship to the documents split out of it
    is discoverable via `Document.split_from_document_id` on the children (and
    `GET /documents/{id}` echoes `child_document_ids` on the parent).
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
    classify = "classify"
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
    doc_type: Mapped[str | None] = mapped_column(String, nullable=True)  # set by ClassifyStage; null until classified
    status: Mapped[str] = mapped_column(String, nullable=False, default=DocumentStatus.uploaded.value)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Set on a document created by page-splitting (Phase 4) - see ClassifyStage.
    split_from_document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    # Non-null only when this document is one group of a split upload - restricts
    # which of its own `pages` rows belong to it. Null means "all of them."
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    image_key: Mapped[str | None] = mapped_column(String, nullable=True)  # rendered page PNG, for the review UI
    words: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # [{"text": str, "bbox": [x0,y0,x1,y1]}, ...]
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
    """One row per extracted field. `page_no`/`bbox`/`source_text` are the
    deterministically-grounded location (app/pipeline/grounding.py) - null if
    the field itself is null or grounding couldn't find the model's reported
    source_text on the page. `confidence` is the final per-field score from
    the Phase 9 confidence gate (grounded + rules-passed + reviewer-agreed +
    no-escalation-needed)."""

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
    corrections: Mapped[list["Correction"]] = relationship(back_populates="field_value", cascade="all, delete-orphan")


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


class Supplier(Base):
    """Stands in for real master data (Phase 7 cross-check): a small, seeded
    per-tenant allowlist of known supplier names. See scripts/seed_suppliers.py."""

    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_supplier_tenant_name"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Correction(Base):
    """A human edit made via the review UI (app/api/review.py) - written when
    a reviewer corrects a field and approves the document."""

    __tablename__ = "corrections"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    field_value_id: Mapped[str] = mapped_column(ForeignKey("field_values.id"), nullable=False, index=True)
    old_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    field_value: Mapped["FieldValue"] = relationship(back_populates="corrections")


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
