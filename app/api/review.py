"""Minimal review surface: JSON list of documents needing a human look.

A real review UI (page-image + bounding-box highlighting + editable fields,
writing to `corrections`) is Phase 10 and out of scope here - this is the
"JSON via the API is enough for now" version the plan's own must-have list
allows for.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import Document, DocumentStatus, get_db

router = APIRouter()


@router.get("/review")
def list_needs_review(tenant_id: str | None = None, db: Session = Depends(get_db)):
    query = db.query(Document).filter(Document.status == DocumentStatus.needs_review.value)
    if tenant_id is not None:
        query = query.filter(Document.tenant_id == tenant_id)
    documents = query.order_by(Document.created_at).all()

    return {
        "count": len(documents),
        "documents": [
            {
                "document_id": d.id,
                "tenant_id": d.tenant_id,
                "filename": d.filename,
                "confidence": d.confidence,
                "created_at": d.created_at.isoformat(),
            }
            for d in documents
        ],
    }
