"""LLM-based structured extraction.

The cheap model runs first and fills the `Invoice` schema via forced tool-use
(so we always get well-formed JSON back, not prose to parse). On escalation
(app/pipeline/review.py) the strong model gets the same document plus the list
of validation issues the cheap model's answer failed, and is asked to fix them.

We tell the model explicitly to use null instead of guessing - that's the
single biggest lever against hallucinated totals/dates on messy invoices.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from google import genai
from pydantic import ValidationError

from app.models import Invoice, Issue

SYSTEM_PROMPT = """You extract structured data from invoice text (which may include OCR errors).
Only report a value if it is actually present in the text. If a field is missing, ambiguous, or
you are not confident, set it to null - never guess or infer a value that is not written down,
except for currency, which may be inferred from context (e.g. "$" -> USD, a UK address -> GBP)
only when reasonably unambiguous. Dates must be normalized to YYYY-MM-DD. Numbers must be plain
numbers with no currency symbols or thousands separators."""

INVOICE_TOOL_NAME = "record_invoice"

# Gemini's function-calling schema is OpenAPI-style (nullable: true), not JSON
# Schema's `type: [string, null]` union form.
INVOICE_TOOL = {
    "type": "function",
    "name": INVOICE_TOOL_NAME,
    "description": "Record the structured invoice fields extracted from the document text.",
    "parameters": {
        "type": "object",
        "properties": {
            "supplier": {"type": "string", "nullable": True},
            "invoice_number": {"type": "string", "nullable": True},
            "invoice_date": {"type": "string", "nullable": True, "description": "YYYY-MM-DD or null"},
            "due_date": {"type": "string", "nullable": True, "description": "YYYY-MM-DD or null"},
            "currency": {"type": "string", "nullable": True, "description": "ISO 4217 code, e.g. USD, EUR, GBP"},
            "subtotal": {"type": "number", "nullable": True},
            "tax": {"type": "number", "nullable": True},
            "total": {"type": "number", "nullable": True},
            "line_items": {
                "type": "array",
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


@dataclass
class ExtractionCall:
    invoice: Invoice
    pre_issues: list[Issue]  # issues raised while coercing the raw LLM output itself
    model: str
    cost_usd: float
    latency_ms: int
    raw: dict


def safe_build_invoice(raw: dict) -> tuple[Invoice, list[Issue]]:
    """Build an Invoice from raw LLM JSON, degrading field-by-field instead of
    failing the whole extraction if one field (usually a date) is malformed.

    Uses pydantic's error locations to null out exactly the field(s) that
    failed to validate, re-checking until the payload validates or we run out
    of fields to drop.
    """
    cleaned = dict(raw)
    issues: list[Issue] = []

    for _ in range(len(cleaned) + 1):
        try:
            return Invoice.model_validate(cleaned), issues
        except ValidationError as exc:
            progressed = False
            for err in exc.errors():
                loc = err["loc"]
                if not loc:
                    continue
                top_field = loc[0]
                if top_field == "line_items" and len(loc) >= 3:
                    idx, item_field = loc[1], loc[2]
                    items = cleaned.get("line_items") or []
                    if isinstance(idx, int) and idx < len(items) and items[idx].get(item_field) is not None:
                        items[idx][item_field] = None
                        progressed = True
                elif top_field in cleaned and cleaned[top_field] is not None:
                    issues.append(
                        Issue(
                            code="unparseable_field",
                            message=f"Field '{top_field}' had an invalid value and was dropped: {raw.get(top_field)!r}",
                            severity="warning",
                        )
                    )
                    cleaned[top_field] = [] if top_field == "line_items" else None
                    progressed = True
            if not progressed:
                raise

    raise RuntimeError("safe_build_invoice: failed to converge on a valid Invoice")


def extract_invoice(
    client: genai.Client,
    *,
    model: str,
    document_text: str,
    price_in_per_million: float,
    price_out_per_million: float,
    prior_issues: list[str] | None = None,
) -> ExtractionCall:
    user_content = f"Invoice document text:\n\n{document_text}"
    if prior_issues:
        issues_block = "\n".join(f"- {i}" for i in prior_issues)
        user_content += (
            "\n\nA previous extraction attempt failed these validation checks:\n"
            f"{issues_block}\n\n"
            "Re-read the document carefully and correct these specific problems. "
            "If a value genuinely isn't in the document, use null rather than forcing a fix."
        )

    start = time.monotonic()
    interaction = client.interactions.create(
        model=model,
        system_instruction=SYSTEM_PROMPT,
        input=user_content,
        tools=[INVOICE_TOOL],
        generation_config={
            "tool_choice": {"allowed_tools": {"mode": "any", "tools": [INVOICE_TOOL_NAME]}},
        },
    )
    latency_ms = int((time.monotonic() - start) * 1000)

    call_step = next(s for s in interaction.steps if s.type == "function_call")
    raw = call_step.arguments

    usage = interaction.usage
    cost_usd = ((usage.total_input_tokens or 0) / 1_000_000) * price_in_per_million + (
        (usage.total_output_tokens or 0) / 1_000_000
    ) * price_out_per_million

    invoice, pre_issues = safe_build_invoice(raw)

    return ExtractionCall(
        invoice=invoice,
        pre_issues=pre_issues,
        model=model,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        raw=raw,
    )
