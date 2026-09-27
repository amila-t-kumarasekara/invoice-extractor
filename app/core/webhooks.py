"""Export webhook (NEW_PLAN.md Phase 10): fired best-effort when a document
becomes `approved`. Failure is logged, never raised - a broken webhook
endpoint on the receiving end should not turn an otherwise-successful
`validate` stage into a retry loop.
"""
from __future__ import annotations

import logging

import httpx

from app.core.config import Settings
from app.core.db import Document

logger = logging.getLogger("webhooks")


def fire_approved_webhook(settings: Settings, document: Document, data: dict) -> None:
    if not settings.webhook_url:
        return
    payload = {
        "event": "document.approved",
        "document_id": document.id,
        "tenant_id": document.tenant_id,
        "doc_type": document.doc_type,
        "confidence": document.confidence,
        "data": data,
    }
    try:
        httpx.post(settings.webhook_url, json=payload, timeout=settings.webhook_timeout_seconds)
    except httpx.HTTPError as exc:
        logger.warning("webhook delivery failed for document=%s: %s", document.id, exc)
