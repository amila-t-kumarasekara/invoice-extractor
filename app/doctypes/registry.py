"""Doctype registry (NEW_PLAN.md Phase 5): maps a document type name to
everything the pipeline needs to process it - schema, prompt, tool schema,
and validators. Only `invoice` is implemented; `ClassifyStage` routes anything
else to `doc_type="unknown"` + `needs_review` rather than guessing at a
schema/prompt that doesn't exist. Adding a new document type means adding a
new `app/doctypes/<name>/` package and one more `REGISTRY` entry - not new
pipeline code.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.doctypes.invoice.rules import cross_check_invoice, validate_invoice
from app.doctypes.invoice.schema import ALL_FIELDS, GROUNDED_FIELDS, REQUIRED_FIELDS, Invoice
from app.doctypes.invoice.tool_schema import INVOICE_TOOL, INVOICE_TOOL_NAME
from app.models import Issue


@dataclass(frozen=True)
class DocTypeConfig:
    name: str
    schema: type[BaseModel]
    system_prompt: str
    tool_schema: dict
    tool_name: str
    all_fields: tuple[str, ...]
    required_fields: tuple[str, ...]
    grounded_fields: tuple[str, ...]
    validate_fn: Callable[[BaseModel], list[Issue]]
    cross_check_fn: Callable[[Session, str, str, BaseModel], list[Issue]]


_INVOICE_PROMPT = (Path(__file__).parent / "invoice" / "prompt.md").read_text()

REGISTRY: dict[str, DocTypeConfig] = {
    "invoice": DocTypeConfig(
        name="invoice",
        schema=Invoice,
        system_prompt=_INVOICE_PROMPT,
        tool_schema=INVOICE_TOOL,
        tool_name=INVOICE_TOOL_NAME,
        all_fields=ALL_FIELDS,
        required_fields=REQUIRED_FIELDS,
        grounded_fields=GROUNDED_FIELDS,
        validate_fn=validate_invoice,
        cross_check_fn=cross_check_invoice,
    ),
}
