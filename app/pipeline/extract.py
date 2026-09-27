"""Doctype-generic, grounded LLM extraction.

The cheap model runs first and fills the doctype's Pydantic schema via forced
tool-use (so we always get well-formed JSON back, not prose to parse). On
escalation (app/pipeline/orchestrator.py's ValidateStage) the strong model
gets the same document plus the list of validation issues the cheap model's
answer failed, and is asked to fix them.

Grounding (Phase 6): every top-level scalar field comes back wrapped as
`{value, page_no, source_text}` instead of a bare value (see
app/doctypes/invoice/tool_schema.py). `unwrap_grounded_raw` splits that into a
plain value dict (fed to `safe_build_invoice`, unchanged from before) and a
dict of grounding *hints* - the actual bbox resolution against real page word
boxes happens in app/pipeline/grounding.py, called from the orchestrator where
the parsed page data lives.

We tell the model explicitly to use null instead of guessing - that's the
single biggest lever against hallucinated totals/dates on messy invoices.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from google import genai
from pydantic import BaseModel, ValidationError

from app.doctypes.registry import DocTypeConfig
from app.models import Issue


@dataclass
class ExtractionCall:
    record: BaseModel  # the doctype's schema instance (e.g. Invoice)
    pre_issues: list[Issue]  # issues raised while coercing the raw LLM output itself
    grounding_hints: dict[str, dict]  # field -> {"page_no": int|None, "source_text": str|None}
    model: str
    cost_usd: float
    latency_ms: int
    raw: dict
    input_tokens: int = 0
    output_tokens: int = 0


def unwrap_grounded_raw(raw: dict, grounded_fields: tuple[str, ...]) -> tuple[dict, dict[str, dict]]:
    """Split `{field: {value, page_no, source_text}}` into a plain value dict
    and a grounding-hints dict. Defensive against a field not following the
    wrapper shape (degrades to null rather than crashing)."""
    plain: dict = {}
    grounding_hints: dict[str, dict] = {}
    for key, val in raw.items():
        if key in grounded_fields:
            if isinstance(val, dict) and "value" in val:
                plain[key] = val.get("value")
                grounding_hints[key] = {"page_no": val.get("page_no"), "source_text": val.get("source_text")}
            else:
                plain[key] = None
                grounding_hints[key] = {"page_no": None, "source_text": None}
        else:
            plain[key] = val
    return plain, grounding_hints


def safe_build_invoice(schema_cls: type[BaseModel], raw: dict) -> tuple[BaseModel, list[Issue]]:
    """Build a schema instance from raw (unwrapped) LLM JSON, degrading
    field-by-field instead of failing the whole extraction if one field
    (usually a date) is malformed.

    Uses pydantic's error locations to null out exactly the field(s) that
    failed to validate, re-checking until the payload validates or we run out
    of fields to drop.
    """
    cleaned = dict(raw)
    issues: list[Issue] = []

    for _ in range(len(cleaned) + 1):
        try:
            return schema_cls.model_validate(cleaned), issues
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
                            field=top_field,
                        )
                    )
                    cleaned[top_field] = [] if top_field == "line_items" else None
                    progressed = True
            if not progressed:
                raise

    raise RuntimeError("safe_build_invoice: failed to converge on a valid schema instance")


def extract_document(
    client: genai.Client,
    *,
    doctype: DocTypeConfig,
    model: str,
    document_text: str,
    price_in_per_million: float,
    price_out_per_million: float,
    prior_issues: list[str] | None = None,
) -> ExtractionCall:
    user_content = f"Document text:\n\n{document_text}"
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
        system_instruction=doctype.system_prompt,
        input=user_content,
        tools=[doctype.tool_schema],
        generation_config={
            "tool_choice": {"allowed_tools": {"mode": "any", "tools": [doctype.tool_name]}},
        },
    )
    latency_ms = int((time.monotonic() - start) * 1000)

    call_step = next(s for s in interaction.steps if s.type == "function_call")
    raw = call_step.arguments

    usage = interaction.usage
    input_tokens = usage.total_input_tokens or 0
    output_tokens = usage.total_output_tokens or 0
    cost_usd = (input_tokens / 1_000_000) * price_in_per_million + (output_tokens / 1_000_000) * price_out_per_million

    plain_raw, grounding_hints = unwrap_grounded_raw(raw, doctype.grounded_fields)
    record, pre_issues = safe_build_invoice(doctype.schema, plain_raw)

    return ExtractionCall(
        record=record,
        pre_issues=pre_issues,
        grounding_hints=grounding_hints,
        model=model,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        raw=raw,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
