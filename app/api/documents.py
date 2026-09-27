from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.db import Document, Extraction, FieldValue, Job, Page, ValidationIssue, get_db
from app.core.storage import get_storage
from app.doctypes.registry import REGISTRY
from app.pipeline.extract import unwrap_grounded_raw

router = APIRouter()


def _extraction_response(db: Session, document: Document, extraction: Extraction | None) -> dict | None:
    if extraction is None:
        return None

    issues = db.query(ValidationIssue).filter_by(extraction_id=extraction.id).all()
    field_values = db.query(FieldValue).filter_by(extraction_id=extraction.id).all()

    doctype = REGISTRY.get(document.doc_type)
    if doctype is not None:
        plain_data, _hints = unwrap_grounded_raw(extraction.raw, doctype.grounded_fields)
    else:
        plain_data = extraction.raw

    return {
        "model": extraction.model,
        "attempt_no": extraction.attempt_no,
        "data": plain_data,
        "fields": [
            {
                "field": fv.field,
                "value": fv.value,
                "page_no": fv.page_no,
                "bbox": fv.bbox,
                "confidence": fv.confidence,
            }
            for fv in field_values
        ],
        "issues": [{"field": i.field, "rule": i.rule, "severity": i.severity, "message": i.message} for i in issues],
        "tokens": extraction.tokens,
        "cost_usd": extraction.cost_usd,
        "latency_ms": extraction.latency_ms,
    }


@router.get("/documents")
def list_documents(tenant_id: str | None = None, status: str | None = None, db: Session = Depends(get_db)):
    query = db.query(Document)
    if tenant_id is not None:
        query = query.filter(Document.tenant_id == tenant_id)
    if status is not None:
        query = query.filter(Document.status == status)
    documents = query.order_by(Document.created_at.desc()).limit(200).all()

    return {
        "count": len(documents),
        "documents": [
            {
                "document_id": d.id,
                "tenant_id": d.tenant_id,
                "filename": d.filename,
                "doc_type": d.doc_type,
                "status": d.status,
                "confidence": d.confidence,
                "created_at": d.created_at.isoformat(),
            }
            for d in documents
        ],
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
    children = db.query(Document.id).filter_by(split_from_document_id=document_id).all()
    pages = db.query(Page).filter_by(document_id=document_id).order_by(Page.page_no).all()
    latest_job = db.query(Job).filter_by(document_id=document_id).order_by(Job.created_at.desc()).first()

    return {
        "document_id": document.id,
        "tenant_id": document.tenant_id,
        "status": document.status,
        "doc_type": document.doc_type,
        "confidence": document.confidence,
        "filename": document.filename,
        "created_at": document.created_at.isoformat(),
        "split_from_document_id": document.split_from_document_id,
        "child_document_ids": [c.id for c in children],
        "pages": [{"page_no": p.page_no, "text_source": p.text_source} for p in pages],
        "extraction": _extraction_response(db, document, latest),
        "last_job": None
        if latest_job is None
        else {
            "stage": latest_job.stage,
            "status": latest_job.status,
            "attempts": latest_job.attempts,
            "last_error": latest_job.last_error,
        },
    }


@router.get("/documents/{document_id}/pages/{page_no}/image")
def get_page_image(document_id: str, page_no: int, db: Session = Depends(get_db)):
    page = db.query(Page).filter_by(document_id=document_id, page_no=page_no).first()
    if page is None or page.image_key is None:
        raise HTTPException(status_code=404, detail="page image not found")
    content = get_storage().read(page.image_key)
    return Response(content=content, media_type="image/png")
