"""FastAPI app: upload a PDF, poll for the extraction result.

Idempotency: the unique key is (tenant_id, file_hash). Re-uploading the same
bytes for the same tenant returns the existing document instead of enqueuing a
second job - same trick as an idempotency key on a hotel booking POST, just
computed from the file content instead of a client-supplied header.

Security: every upload is scanned against a deterministic keyword/structure
denylist (app/pipeline/security.py) before it's hashed, stored, or queued. A
hit is rejected outright with 400 - nothing is written to disk and no job is
created, so a malicious file never reaches parsing or the LLM.
"""
from __future__ import annotations

import hashlib
import logging

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import Document, DocumentStatus, Extraction, Job, JobStatus, get_db, init_db
from app.pipeline.security import scan_pdf_bytes

logger = logging.getLogger("api")

app = FastAPI(title="Invoice Extractor")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


def _document_response(document: Document, latest: Extraction | None) -> dict:
    return {
        "document_id": document.id,
        "tenant_id": document.tenant_id,
        "status": document.status,
        "filename": document.filename,
        "created_at": document.created_at.isoformat(),
        "extraction": None
        if latest is None
        else {
            "model": latest.model,
            "data": latest.data,
            "issues": latest.issues,
            "confidence": latest.confidence,
            "cost_usd": latest.cost_usd,
            "latency_ms": latest.latency_ms,
        },
    }


@app.post("/documents")
async def upload_document(
    tenant_id: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")

    security_hits = scan_pdf_bytes(content)
    if security_hits:
        logger.warning(
            "rejected upload tenant=%s filename=%s reasons=%s", tenant_id, file.filename, security_hits
        )
        raise HTTPException(
            status_code=400,
            detail={"error": "file rejected by security scan", "reasons": security_hits},
        )

    file_hash = hashlib.sha256(content).hexdigest()

    existing = db.query(Document).filter_by(tenant_id=tenant_id, file_hash=file_hash).first()
    if existing is not None:
        return {"document_id": existing.id, "status": existing.status, "duplicate": True}

    settings = get_settings()
    tenant_dir = settings.storage_path / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    storage_path = tenant_dir / f"{file_hash}.pdf"
    storage_path.write_bytes(content)

    document = Document(
        tenant_id=tenant_id,
        file_hash=file_hash,
        filename=file.filename or "upload.pdf",
        storage_path=str(storage_path),
        status=DocumentStatus.pending.value,
    )
    db.add(document)
    try:
        db.flush()
    except IntegrityError:
        # Lost a race with a concurrent identical upload for the same tenant.
        db.rollback()
        existing = db.query(Document).filter_by(tenant_id=tenant_id, file_hash=file_hash).first()
        return {"document_id": existing.id, "status": existing.status, "duplicate": True}

    db.add(Job(document_id=document.id, status=JobStatus.queued.value))
    db.commit()

    return {"document_id": document.id, "status": document.status, "duplicate": False}


@app.get("/documents/{document_id}")
def get_document(document_id: str, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")

    latest = (
        db.query(Extraction)
        .filter_by(document_id=document_id)
        .order_by(Extraction.created_at.desc())
        .first()
    )
    return _document_response(document, latest)


@app.get("/", response_class=HTMLResponse)
def upload_page() -> str:
    return """
<!doctype html>
<html>
<head><title>Invoice Extractor</title></head>
<body style="font-family: sans-serif; max-width: 640px; margin: 40px auto;">
  <h2>Invoice Extractor</h2>
  <form id="upload-form">
    <input name="tenant_id" placeholder="tenant id" value="demo-tenant" required />
    <input name="file" type="file" accept="application/pdf" required />
    <button type="submit">Upload</button>
  </form>
  <pre id="result" style="background:#f4f4f4; padding:12px; white-space:pre-wrap;"></pre>
  <script>
    const form = document.getElementById('upload-form');
    const result = document.getElementById('result');
    let pollTimer = null;

    async function poll(id) {
      const res = await fetch(`/documents/${id}`);
      const data = await res.json();
      result.textContent = JSON.stringify(data, null, 2);
      if (['done', 'needs_review', 'failed', 'rejected'].includes(data.status)) {
        clearInterval(pollTimer);
      }
    }

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      clearInterval(pollTimer);
      const body = new FormData(form);
      const res = await fetch('/documents', { method: 'POST', body });
      const data = await res.json();
      result.textContent = JSON.stringify(data, null, 2);
      pollTimer = setInterval(() => poll(data.document_id), 1500);
    });
  </script>
</body>
</html>
"""
