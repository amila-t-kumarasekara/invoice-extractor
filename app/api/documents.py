from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import Document, Extraction, ValidationIssue, get_db

router = APIRouter()


def _extraction_response(db: Session, extraction: Extraction | None) -> dict | None:
    if extraction is None:
        return None
    issues = db.query(ValidationIssue).filter_by(extraction_id=extraction.id).all()
    return {
        "model": extraction.model,
        "attempt_no": extraction.attempt_no,
        "data": extraction.raw,
        "issues": [
            {"field": i.field, "rule": i.rule, "severity": i.severity, "message": i.message} for i in issues
        ],
        "tokens": extraction.tokens,
        "cost_usd": extraction.cost_usd,
        "latency_ms": extraction.latency_ms,
    }


@router.get("/documents/{document_id}")
def get_document(document_id: str, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")

    latest = (
        db.query(Extraction)
        .filter_by(document_id=document_id)
        .order_by(Extraction.attempt_no.desc())
        .first()
    )

    return {
        "document_id": document.id,
        "tenant_id": document.tenant_id,
        "status": document.status,
        "doc_type": document.doc_type,
        "confidence": document.confidence,
        "filename": document.filename,
        "created_at": document.created_at.isoformat(),
        "extraction": _extraction_response(db, latest),
    }
