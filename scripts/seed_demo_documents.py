"""Seeds a handful of realistic-looking documents under tenant "demo" so the
UI has something worth looking at without needing a live GEMINI_API_KEY or a
full pipeline run. Idempotent-ish: deletes its own previous demo rows (by
file_hash prefix) before recreating them.

Usage: python -m scripts.seed_demo_documents
"""
from __future__ import annotations

from datetime import date

import fitz

from app.core.db import (
    Document,
    DocumentStatus,
    Extraction,
    FieldValue,
    Job,
    JobStatus,
    Page,
    ValidationIssue,
    get_sessionmaker,
)
from app.core.storage import get_storage
from app.pipeline.grounding import find_bbox
from app.pipeline.parse import parse_pdf

TENANT = "demo"
HASH_PREFIX = "seed-demo-"


def _make_pdf(lines: list[str]) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontsize=12)
        y += 22
    data = doc.tobytes()
    doc.close()
    return data


def _store_and_parse(file_hash: str, content: bytes):
    storage_key = get_storage().save(TENANT, f"{file_hash}.pdf", content)
    tmp_path = get_storage().path_for_local_tools(storage_key)
    parsed = parse_pdf(tmp_path, min_chars_per_page=20)
    return storage_key, parsed


def _seed_page_rows(db, document_id: str, parsed) -> None:
    storage = get_storage()
    for page in parsed.pages:
        image_key = storage.save(TENANT, f"{document_id}_page{page.page_number}.png", page.image_bytes)
        db.add(
            Page(
                document_id=document_id,
                page_no=page.page_number,
                text_source=page.source,
                text=page.text,
                image_key=image_key,
                words=[w.to_json() for w in page.words],
            )
        )
    db.commit()


def _bbox_on(parsed, page_no: int, text: str):
    words = [w.to_json() for w in parsed.pages[page_no - 1].words]
    return find_bbox(words, text)


def seed_approved(db) -> None:
    file_hash = f"{HASH_PREFIX}approved"
    content = _make_pdf(
        [
            "Acme Co",
            "Invoice INV-2024-0142",
            "Invoice Date: 2026-01-15",
            "Due Date: 2026-02-14",
            "Currency: USD",
            "Widget A       qty 4   unit 25.00   amount 100.00",
            "Widget B       qty 2   unit 60.00    amount 120.00",
            "Subtotal: 220.00",
            "Tax: 17.60",
            "Total: 237.60",
        ]
    )
    storage_key, parsed = _store_and_parse(file_hash, content)

    document = Document(
        tenant_id=TENANT,
        file_hash=file_hash,
        filename="acme-invoice-0142.pdf",
        mime_type="application/pdf",
        storage_key=storage_key,
        doc_type="invoice",
        status=DocumentStatus.approved.value,
        confidence=0.97,
    )
    db.add(document)
    db.flush()
    _seed_page_rows(db, document.id, parsed)

    extraction = Extraction(
        document_id=document.id, model="gemini-3.5-flash", attempt_no=1,
        raw={}, tokens={"input": 812, "output": 96}, cost_usd=0.00071, latency_ms=642,
    )
    db.add(extraction)
    db.flush()

    fields = {
        "supplier": ("Acme Co", "Acme Co", 0.97),
        "invoice_number": ("INV-2024-0142", "INV-2024-0142", 0.98),
        "invoice_date": ("2026-01-15", "2026-01-15", 0.98),
        "due_date": ("2026-02-14", "2026-02-14", 0.96),
        "currency": ("USD", "USD", 0.95),
        "subtotal": (220.0, "220.00", 0.96),
        "tax": (17.6, "17.60", 0.94),
        "total": (237.6, "237.60", 0.97),
    }
    for field, (value, source_text, confidence) in fields.items():
        bbox = _bbox_on(parsed, 1, source_text)
        db.add(FieldValue(
            extraction_id=extraction.id, field=field, value=value,
            page_no=1 if bbox else None, bbox=bbox, source_text=source_text, confidence=confidence,
        ))
    db.commit()
    print(f"seeded approved document: {document.id}")


def seed_needs_review(db) -> None:
    file_hash = f"{HASH_PREFIX}needs-review"
    content = _make_pdf(
        [
            "Riverside Fabrication Ltd",
            "Invoice INV-5591",
            "Invoice Date: 04/03/2026",
            "Currency: EUR",
            "Steel brackets   qty 40  unit 3.20   amount 128.00",
            "Subtotal: 128.00",
            "Tax: 12.00",
            "Total: 148.00",
        ]
    )
    storage_key, parsed = _store_and_parse(file_hash, content)

    document = Document(
        tenant_id=TENANT,
        file_hash=file_hash,
        filename="riverside-invoice-5591.pdf",
        mime_type="application/pdf",
        storage_key=storage_key,
        doc_type="invoice",
        status=DocumentStatus.needs_review.value,
        confidence=0.44,
    )
    db.add(document)
    db.flush()
    _seed_page_rows(db, document.id, parsed)

    extraction = Extraction(
        document_id=document.id, model="gemini-3.5-pro", attempt_no=2,
        raw={}, tokens={"input": 1340, "output": 140}, cost_usd=0.00312, latency_ms=1810,
    )
    db.add(extraction)
    db.flush()

    # subtotal (128.00) + tax (12.00) = 140.00, but total says 148.00 -> mismatch (this is the "why" for needs_review)
    fields = {
        "supplier": ("Riverside Fabrication Ltd", "Riverside Fabrication Ltd", 0.9),
        "invoice_number": ("INV-5591", "INV-5591", 0.92),
        "invoice_date": ("2026-03-04", "04/03/2026", 0.4),  # ambiguous date, low confidence
        "due_date": (None, None, 1.0),
        "currency": ("EUR", "EUR", 0.85),
        "subtotal": (128.0, "128.00", 0.9),
        "tax": (12.0, "12.00", 0.9),
        "total": (148.0, None, 0.2),  # not grounded - model couldn't find a matching source_text
    }
    for field, (value, source_text, confidence) in fields.items():
        bbox = _bbox_on(parsed, 1, source_text) if source_text else None
        db.add(FieldValue(
            extraction_id=extraction.id, field=field, value=value,
            page_no=1 if bbox else None, bbox=bbox, source_text=source_text, confidence=confidence,
        ))

    db.add(ValidationIssue(
        extraction_id=extraction.id, field="total", rule="total_mismatch", severity="error",
        message="subtotal (128.00) + tax (12.00) = 140.00 but total is 148.00",
    ))
    db.add(ValidationIssue(
        extraction_id=extraction.id, field="supplier", rule="unknown_supplier", severity="warning",
        message="Supplier 'Riverside Fabrication Ltd' is not in the known-suppliers list for this tenant",
    ))
    db.commit()
    print(f"seeded needs_review document: {document.id}")


def seed_rejected(db) -> None:
    file_hash = f"{HASH_PREFIX}rejected"
    document = Document(
        tenant_id=TENANT, file_hash=file_hash, filename="suspicious-upload.pdf",
        mime_type="application/pdf", storage_key=f"{TENANT}/{file_hash}.pdf",
        status=DocumentStatus.rejected.value,
    )
    db.add(document)
    db.flush()
    db.add(Job(
        document_id=document.id, stage="parse", status=JobStatus.failed.value, attempts=0,
        last_error="blocked by security scan: document text contains suspicious phrase: 'ignore previous instructions'",
    ))
    db.commit()
    print(f"seeded rejected document: {document.id}")


def seed_failed(db) -> None:
    file_hash = f"{HASH_PREFIX}failed"
    document = Document(
        tenant_id=TENANT, file_hash=file_hash, filename="corrupted-network-blip.pdf",
        mime_type="application/pdf", storage_key=f"{TENANT}/{file_hash}.pdf",
        doc_type="invoice", status=DocumentStatus.failed.value,
    )
    db.add(document)
    db.flush()
    db.add(Job(
        document_id=document.id, stage="extract", status=JobStatus.failed.value, attempts=5,
        last_error="Error code: 503 - The model is overloaded. Please try again later.",
    ))
    db.commit()
    print(f"seeded failed document: {document.id}")


def main() -> None:
    Session = get_sessionmaker()
    db = Session()
    try:
        stale = db.query(Document).filter(
            Document.tenant_id == TENANT, Document.file_hash.like(f"{HASH_PREFIX}%")
        ).all()
        for doc in stale:
            db.delete(doc)
        db.commit()

        seed_approved(db)
        seed_needs_review(db)
        seed_rejected(db)
        seed_failed(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
