"""Upload endpoint.

Idempotency: the unique key is (tenant_id, file_hash). Re-uploading the same
bytes for the same tenant returns the existing document instead of enqueuing a
second job.

Safety (Phase 2): every upload runs through `app.pipeline.safety.scan_upload`
before it's hashed for dedup or written to storage - real content-type
sniffing (not the filename extension), size limit, page-count limit,
encrypted/corrupt-PDF rejection, and the PDF-structure denylist. A hit is
rejected outright with 400; nothing is stored and no job is created.
"""
from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import Document, DocumentStatus, JobStage, JobStatus, get_db
from app.core.db import Job as JobModel
from app.core.storage import get_storage
from app.pipeline.safety import scan_upload, sniff_mime_type

logger = logging.getLogger("api.upload")

router = APIRouter()


@router.post("/documents", status_code=202)
async def upload_document(
    tenant_id: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")

    settings = get_settings()
    safety_hits = scan_upload(
        content,
        max_bytes=settings.max_upload_bytes,
        max_pages=settings.max_pages,
        allowed_mime_types=settings.allowed_mime_types,
    )
    if safety_hits:
        logger.warning("rejected upload tenant=%s filename=%s reasons=%s", tenant_id, file.filename, safety_hits)
        raise HTTPException(status_code=400, detail={"error": "file rejected by safety checks", "reasons": safety_hits})

    file_hash = hashlib.sha256(content).hexdigest()

    existing = db.query(Document).filter_by(tenant_id=tenant_id, file_hash=file_hash).first()
    if existing is not None:
        return {"document_id": existing.id, "status": existing.status, "duplicate": True}

    mime_type = sniff_mime_type(content) or "application/pdf"
    extension = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}.get(mime_type, "bin")
    storage_key = get_storage().save(tenant_id, f"{file_hash}.{extension}", content)

    document = Document(
        tenant_id=tenant_id,
        file_hash=file_hash,
        filename=file.filename or f"upload.{extension}",
        mime_type=mime_type,
        storage_key=storage_key,
        doc_type="invoice",  # Phase 5 (classify) will set this properly; everything is assumed invoice for now
        status=DocumentStatus.uploaded.value,
    )
    db.add(document)
    try:
        db.flush()
    except IntegrityError:
        # Lost a race with a concurrent identical upload for the same tenant.
        db.rollback()
        existing = db.query(Document).filter_by(tenant_id=tenant_id, file_hash=file_hash).first()
        return {"document_id": existing.id, "status": existing.status, "duplicate": True}

    db.add(JobModel(document_id=document.id, stage=JobStage.parse.value, status=JobStatus.queued.value))
    db.commit()

    return {"document_id": document.id, "status": document.status, "duplicate": False}
