"""Gemini function-calling schema for grounded invoice extraction.

Every top-level scalar field is wrapped as `{value, page_no, source_text}`
instead of a bare value, so the model tells us *where* it read something, not
just what it read. `app/pipeline/grounding.py` treats `page_no`/`source_text`
as hints only - it re-derives the actual bounding box deterministically by
searching the page's own word boxes, never trusting model-reported
coordinates (the model has no way to know pixel positions; it only sees text).
"""
from __future__ import annotations

INVOICE_TOOL_NAME = "record_invoice"


def _grounded_field(value_type: str, description: str | None = None) -> dict:
    value_schema: dict = {"type": value_type, "nullable": True}
    if description:
        value_schema["description"] = description
    return {
        "type": "object",
        "properties": {
            "value": value_schema,
            "page_no": {"type": "integer", "nullable": True, "description": "1-based page number, or null"},
            "source_text": {
                "type": "string",
                "nullable": True,
                "description": "verbatim text this value was read from, or null",
            },
        },
        "required": ["value", "page_no", "source_text"],
    }


INVOICE_TOOL: dict = {
    "type": "function",
    "name": INVOICE_TOOL_NAME,
    "description": "Record the structured, grounded invoice fields extracted from the document text.",
    "parameters": {
        "type": "object",
        "properties": {
            "supplier": _grounded_field("string"),
            "invoice_number": _grounded_field("string"),
            "invoice_date": _grounded_field("string", "YYYY-MM-DD or null"),
            "due_date": _grounded_field("string", "YYYY-MM-DD or null"),
            "currency": _grounded_field("string", "ISO 4217 code, e.g. USD, EUR, GBP"),
            "subtotal": _grounded_field("number"),
            "tax": _grounded_field("number"),
            "total": _grounded_field("number"),
            "line_items": {
                "type": "array",
                "description": "Ungrounded - cross-checked arithmetically against subtotal instead.",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "nullable": True},
                        "quantity": {"type": "number", "nullable": True},
                        "unit_price": {"type": "number", "nullable": True},
                        "amount": {"type": "number", "nullable": True},
                    },
                    "required": ["description", "quantity", "unit_price", "amount"],
                },
            },
        },
        "required": [
            "supplier",
            "invoice_number",
            "invoice_date",
            "due_date",
            "currency",
            "subtotal",
            "tax",
            "total",
            "line_items",
        ],
    },
}
