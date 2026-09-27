"""Review surface (NEW_PLAN.md Phase 10).

`GET /review` - JSON list of documents needing a human look.
`GET /review/{id}` - an HTML page: each page's rendered image with bounding-box
highlights over every grounded field, and an editable form pre-filled with the
current values. Submitting writes a `Correction` row per changed field (audit
trail / future few-shot examples), updates that field's value, and approves
the document.
`POST /review/{id}/correct` - the form's target; also usable directly as an API.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import Correction, Document, DocumentStatus, Extraction, FieldValue, Page, get_db
from app.core.webhooks import fire_approved_webhook

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


def _render_review_page(data: dict) -> str:
    return f"""
<!doctype html>
<html>
<head>
<title>Review {data['document_id']}</title>
<style>
  body {{ font-family: sans-serif; max-width: 900px; margin: 24px auto; }}
  .page-wrap {{ position: relative; display: inline-block; margin-bottom: 16px; border: 1px solid #ccc; }}
  .page-wrap img {{ display: block; max-width: 100%; height: auto; }}
  .bbox {{ position: absolute; border: 2px solid; box-sizing: border-box; pointer-events: none; }}
  .bbox.high {{ border-color: #2a9d3f; }}
  .bbox.mid {{ border-color: #e0a800; }}
  .bbox.low {{ border-color: #d9363e; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
  td, th {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left; font-size: 14px; }}
  input {{ width: 100%; box-sizing: border-box; padding: 4px; }}
  #result {{ background: #f4f4f4; padding: 12px; white-space: pre-wrap; margin-top: 12px; }}
</style>
</head>
<body>
  <h2>Review document {data['document_id']}</h2>
  <p>status={data['status']}  confidence={data['confidence']}</p>

  <div id="pages"></div>

  <form id="correct-form">
    <table>
      <thead><tr><th>Field</th><th>Value</th><th>Confidence</th></tr></thead>
      <tbody id="fields-body"></tbody>
    </table>
    <p><input id="reviewer" placeholder="Your name" /></p>
    <button type="submit">Save corrections and approve</button>
  </form>
  <pre id="result"></pre>

  <script>
    const DATA = {json.dumps(data)};

    const pagesDiv = document.getElementById('pages');
    for (const page of DATA.pages) {{
      const wrap = document.createElement('div');
      wrap.className = 'page-wrap';
      const img = document.createElement('img');
      img.src = page.image_url;
      wrap.appendChild(img);
      pagesDiv.appendChild(wrap);

      img.addEventListener('load', () => {{
        const fieldsOnPage = DATA.fields.filter(f => f.page_no === page.page_no && f.bbox);
        for (const f of fieldsOnPage) {{
          const [x0, y0, x1, y1] = f.bbox;
          const box = document.createElement('div');
          const cls = f.confidence == null ? 'mid' : (f.confidence >= 0.75 ? 'high' : (f.confidence >= 0.5 ? 'mid' : 'low'));
          box.className = 'bbox ' + cls;
          box.title = f.field;
          box.style.left = (x0 / img.naturalWidth * 100) + '%';
          box.style.top = (y0 / img.naturalHeight * 100) + '%';
          box.style.width = ((x1 - x0) / img.naturalWidth * 100) + '%';
          box.style.height = ((y1 - y0) / img.naturalHeight * 100) + '%';
          wrap.appendChild(box);
        }}
      }});
    }}

    const fieldsBody = document.getElementById('fields-body');
    for (const f of DATA.fields) {{
      if (f.field === 'line_items') continue;
      const tr = document.createElement('tr');
      const value = f.value === null || f.value === undefined ? '' : f.value;
      tr.innerHTML = `<td>${{f.field}}</td>` +
        `<td><input data-field="${{f.field}}" value="${{String(value).replace(/"/g, '&quot;')}}" /></td>` +
        `<td>${{f.confidence == null ? '' : f.confidence.toFixed(2)}}</td>`;
      fieldsBody.appendChild(tr);
    }}

    document.getElementById('correct-form').addEventListener('submit', async (e) => {{
      e.preventDefault();
      const corrections = {{}};
      for (const input of fieldsBody.querySelectorAll('input[data-field]')) {{
        corrections[input.dataset.field] = input.value === '' ? null : input.value;
      }}
      const reviewer = document.getElementById('reviewer').value || null;
      const res = await fetch(`/review/${{DATA.document_id}}/correct`, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{reviewer, corrections}}),
      }});
      document.getElementById('result').textContent = JSON.stringify(await res.json(), null, 2);
    }});
  </script>
</body>
</html>
"""


@router.get("/review/{document_id}", response_class=HTMLResponse)
def review_detail(document_id: str, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")

    pages = db.query(Page).filter_by(document_id=document_id).order_by(Page.page_no).all()
    latest_extraction = (
        db.query(Extraction).filter_by(document_id=document_id).order_by(Extraction.attempt_no.desc()).first()
    )
    field_values = (
        db.query(FieldValue).filter_by(extraction_id=latest_extraction.id).all() if latest_extraction else []
    )

    data = {
        "document_id": document.id,
        "status": document.status,
        "confidence": document.confidence,
        "pages": [{"page_no": p.page_no, "image_url": f"/documents/{document_id}/pages/{p.page_no}/image"} for p in pages],
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
    }
    return HTMLResponse(_render_review_page(data))


class CorrectionPayload(BaseModel):
    reviewer: str | None = None
    corrections: dict[str, object]


@router.post("/review/{document_id}/correct")
def submit_correction(document_id: str, payload: CorrectionPayload, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")

    latest_extraction = (
        db.query(Extraction).filter_by(document_id=document_id).order_by(Extraction.attempt_no.desc()).first()
    )
    if latest_extraction is None:
        raise HTTPException(status_code=400, detail="document has no extraction to correct")

    changed = 0
    for field, new_value in payload.corrections.items():
        field_value = db.query(FieldValue).filter_by(extraction_id=latest_extraction.id, field=field).first()
        if field_value is None or field_value.value == new_value:
            continue
        db.add(
            Correction(
                field_value_id=field_value.id,
                old_value=field_value.value,
                new_value=new_value,
                reviewer=payload.reviewer,
            )
        )
        field_value.value = new_value
        field_value.confidence = 1.0  # human-corrected - fully trusted
        changed += 1

    document.status = DocumentStatus.approved.value
    document.confidence = 1.0
    db.commit()

    final_data = {
        fv.field: fv.value
        for fv in db.query(FieldValue).filter_by(extraction_id=latest_extraction.id).all()
    }
    fire_approved_webhook(get_settings(), document, final_data)

    return {"document_id": document.id, "status": document.status, "fields_corrected": changed}
