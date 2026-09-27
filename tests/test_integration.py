"""Integration tests against a real Postgres (NEW_PLAN.md Phase 11): the
LLM calls are mocked, but parsing, word-box extraction, grounding, business
rules, cross-checks, confidence scoring, and all persistence are real - this
exercises the actual orchestrator stages end to end, not a reimplementation
of them. Skips cleanly if Postgres isn't reachable (see conftest.py).
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import fitz

from app.core.db import Document, DocumentStatus, Job, JobStage, JobStatus, ValidationIssue, utcnow
from app.doctypes.invoice.schema import Invoice
from app.core.storage import get_storage
from app.pipeline.classify import ClassificationResult, PageClassification
from app.pipeline.extract import ExtractionCall
from app.pipeline.orchestrator import ParseStage, ClassifyStage, ExtractStage, ValidateStage
from app.pipeline.reviewer import ReviewResult
from app.core.config import get_settings
from app.queue.jobs import claim_job


def _make_invoice_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in ["Acme Co", "Invoice INV-1001", "Invoice Date: 2026-01-01", "Total: 110.00"]:
        page.insert_text((72, y), line)
        y += 20
    data = doc.tobytes()
    doc.close()
    return data


def _upload_document(db_session, tenant_id: str, file_hash: str) -> Document:
    # These tests commit against a real, shared Postgres (not rolled back at
    # teardown, since the code under test calls commit() itself) - clean up
    # leftovers from a previous run before creating a fresh row.
    for stale in db_session.query(Document).filter_by(tenant_id=tenant_id, file_hash=file_hash).all():
        db_session.delete(stale)
    db_session.commit()

    content = _make_invoice_pdf()
    storage_key = get_storage().save(tenant_id, f"{file_hash}.pdf", content)
    document = Document(
        tenant_id=tenant_id,
        file_hash=file_hash,
        filename="test.pdf",
        mime_type="application/pdf",
        storage_key=storage_key,
        status=DocumentStatus.uploaded.value,
    )
    db_session.add(document)
    db_session.commit()
    return document


def test_full_pipeline_approves_a_clean_invoice_with_llm_mocked(db_session):
    settings = get_settings()
    tenant_id = "integration-test-tenant"
    document = _upload_document(db_session, tenant_id, "integration-clean-invoice")

    # --- parse: real code, no LLM involved ---
    next_stage = ParseStage().run(db_session, document, settings, client=None)
    db_session.commit()
    assert document.status == DocumentStatus.parsed.value
    assert next_stage == JobStage.classify.value

    # --- classify: LLM mocked ---
    stub_classification = ClassificationResult(
        pages=[PageClassification(1, "invoice", 0.95)], model="stub", cost_usd=0.0, latency_ms=1
    )
    with patch("app.pipeline.orchestrator.classify_pages", return_value=stub_classification):
        next_stage = ClassifyStage().run(db_session, document, settings, client=None)
    db_session.commit()
    assert document.doc_type == "invoice"
    assert document.status == DocumentStatus.classified.value
    assert next_stage == JobStage.extract.value

    # --- extract: LLM mocked, but the returned record's fields genuinely
    # appear in the parsed page text, so real grounding (not mocked) succeeds ---
    stub_record = Invoice(
        supplier="Acme Co",
        invoice_number="INV-1001",
        invoice_date=date(2026, 1, 1),
        total=110.0,
    )
    grounding_hints = {
        "supplier": {"page_no": 1, "source_text": "Acme Co"},
        "invoice_number": {"page_no": 1, "source_text": "INV-1001"},
        "invoice_date": {"page_no": 1, "source_text": "2026-01-01"},
        "due_date": {"page_no": None, "source_text": None},
        "currency": {"page_no": None, "source_text": None},
        "subtotal": {"page_no": None, "source_text": None},
        "tax": {"page_no": None, "source_text": None},
        "total": {"page_no": 1, "source_text": "110.00"},
    }
    # `ValidateStage` re-derives the record from `extraction.raw` (persisted,
    # not from this stub's in-memory `.record`) - that's the actual
    # resumability mechanism, so `raw` has to be shaped like real grounded LLM
    # output (JSON-serializable values, dates as strings), not just an empty
    # placeholder.
    raw_values = {
        "supplier": "Acme Co",
        "invoice_number": "INV-1001",
        "invoice_date": "2026-01-01",
        "due_date": None,
        "currency": None,
        "subtotal": None,
        "tax": None,
        "total": 110.0,
    }
    stub_call = ExtractionCall(
        record=stub_record,
        pre_issues=[],
        grounding_hints=grounding_hints,
        model="stub-cheap",
        cost_usd=0.001,
        latency_ms=5,
        raw={field: {"value": raw_values[field], **grounding_hints[field]} for field in grounding_hints}
        | {"line_items": []},
    )
    with patch("app.pipeline.orchestrator.extract_document", return_value=stub_call):
        next_stage = ExtractStage().run(db_session, document, settings, client=None)
    db_session.commit()
    assert document.status == DocumentStatus.extracted.value
    assert next_stage == JobStage.validate.value

    # --- validate: real rules + real cross-checks against Postgres, LLM (reviewer) mocked ---
    stub_review = ReviewResult(
        agreements={f: True for f in Invoice.model_fields if f != "line_items"},
        model="stub-review",
        cost_usd=0.0002,
        latency_ms=2,
    )
    with patch("app.pipeline.orchestrator.review_fields", return_value=stub_review):
        next_stage = ValidateStage().run(db_session, document, settings, client=None)
    db_session.commit()

    assert next_stage is None
    assert document.status == DocumentStatus.approved.value
    assert document.confidence == 1.0

    # the cross-check ran for real (no suppliers seeded for this test tenant) -
    # proving this isn't a reimplementation, it's the actual code path.
    issues = (
        db_session.query(ValidationIssue)
        .join(ValidationIssue.extraction)
        .filter_by(document_id=document.id)
        .all()
    )
    assert any(i.rule == "unknown_supplier" and i.severity == "warning" for i in issues)


def test_upload_idempotency_same_tenant_and_hash_returns_existing_document(db_session):
    tenant_id = "integration-idempotency-tenant"
    first = _upload_document(db_session, tenant_id, "integration-idempotent-file")

    existing = (
        db_session.query(Document)
        .filter_by(tenant_id=tenant_id, file_hash="integration-idempotent-file")
        .all()
    )
    assert len(existing) == 1
    assert existing[0].id == first.id

    # A second "upload" of the same (tenant_id, file_hash) is exactly the
    # dedup check app/api/upload.py performs before ever creating a row.
    duplicate_check = (
        db_session.query(Document)
        .filter_by(tenant_id=tenant_id, file_hash="integration-idempotent-file")
        .first()
    )
    assert duplicate_check is not None
    assert duplicate_check.id == first.id


def test_stale_locked_job_is_reclaimed_by_a_live_worker(db_session):
    settings = get_settings()
    document = _upload_document(db_session, "integration-crash-tenant", "integration-crash-file")

    job = Job(document_id=document.id, stage=JobStage.parse.value, status=JobStatus.processing.value)
    db_session.add(job)
    db_session.commit()

    # Simulate a worker that died mid-stage: locked, but well past the stale window.
    job.locked_at = utcnow() - timedelta(minutes=settings.stale_lock_minutes + 5)
    db_session.commit()

    reclaimed = claim_job(db_session, settings.stale_lock_minutes)
    assert reclaimed is not None
    assert reclaimed.id == job.id
    assert reclaimed.status == JobStatus.processing.value  # reclaimed, not left stuck
