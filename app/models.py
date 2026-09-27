"""Shared, doctype-agnostic schemas. Per-doctype schemas (Invoice, etc.) live
under app/doctypes/<name>/schema.py - see app/doctypes/registry.py.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class Issue(BaseModel):
    code: str
    message: str
    severity: str = "error"  # "error" | "warning"
    field: Optional[str] = None  # which schema field this issue is about, if any
