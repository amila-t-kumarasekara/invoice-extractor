from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def upload_page() -> str:
    return """
<!doctype html>
<html>
<head><title>Invoice Extractor</title></head>
<body style="font-family: sans-serif; max-width: 640px; margin: 40px auto;">
  <h2>Invoice Extractor</h2>
  <form id="upload-form">
    <input name="tenant_id" placeholder="tenant id" value="demo-tenant" required />
    <input name="file" type="file" accept="application/pdf,image/png,image/jpeg" required />
    <button type="submit">Upload</button>
  </form>
  <pre id="result" style="background:#f4f4f4; padding:12px; white-space:pre-wrap;"></pre>
  <script>
    const form = document.getElementById('upload-form');
    const result = document.getElementById('result');
    let pollTimer = null;
    const TERMINAL_STATUSES = ['approved', 'needs_review', 'failed', 'rejected'];

    async function poll(id) {
      const res = await fetch(`/documents/${id}`);
      const data = await res.json();
      result.textContent = JSON.stringify(data, null, 2);
      if (TERMINAL_STATUSES.includes(data.status)) {
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
      if (data.document_id) {
        pollTimer = setInterval(() => poll(data.document_id), 1500);
      }
    });
  </script>
</body>
</html>
"""
