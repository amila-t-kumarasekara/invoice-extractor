"""Escalation and confidence scoring.

Orchestrates: extract with the cheap model -> validate -> if any rule *errors*
(not warnings) came back, re-run with the strong model and hand it the exact
list of failures so it knows what to fix -> validate again -> score confidence.

This is the model-routing story in miniature: pay for the strong model only on
the invoices that actually need it, and always be able to say which model
produced the answer that shipped.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from google import genai

from app.config import Settings
from app.models import ALL_FIELDS, Invoice, Issue
from app.pipeline.extract import ExtractionCall, extract_invoice
from app.pipeline.validate import validate_invoice

# Canonical set of rule categories used to compute the "checks passed" component
# of the confidence score. Keeping this as a fixed list (rather than "however
# many issues came back") makes the score comparable across documents.
CHECK_CODES = (
    "missing_required_field",
    "line_items_subtotal_mismatch",
    "total_mismatch",
    "due_date_before_invoice_date",
    "invalid_currency",
    "unparseable_field",
)


@dataclass
class PipelineAttempt:
    """One model call plus the validation result for that call's output.

    Kept separate from ReviewOutcome because each attempt becomes its own row
    in the `extractions` table - we keep the superseded cheap-model attempt
    around instead of overwriting it, so "which model produced the final
    answer, and what did the first pass get wrong" is answerable from the DB.
    """

    call: ExtractionCall
    issues: list[Issue]
    confidence: float


@dataclass
class ReviewOutcome:
    invoice: Invoice
    issues: list[Issue]
    confidence: float
    model: str
    escalated: bool
    status: str  # "done" | "needs_review"
    cost_usd: float
    latency_ms: int
    attempts: list[PipelineAttempt] = field(default_factory=list)


def compute_confidence(invoice: Invoice, issues: list[Issue], escalated: bool) -> float:
    errors = {i.code for i in issues if i.severity == "error"} & set(CHECK_CODES)
    checks_passed_ratio = 1 - (len(errors) / len(CHECK_CODES))

    filled = sum(1 for f in ALL_FIELDS if getattr(invoice, f) is not None)
    fields_filled_ratio = filled / len(ALL_FIELDS)

    escalation_score = 0.0 if escalated else 1.0

    confidence = 0.5 * checks_passed_ratio + 0.3 * fields_filled_ratio + 0.2 * escalation_score
    return round(max(0.0, min(1.0, confidence)), 4)


def run_pipeline(
    client: genai.Client,
    settings: Settings,
    document_text: str,
) -> ReviewOutcome:
    cheap_call = extract_invoice(
        client,
        model=settings.cheap_model,
        document_text=document_text,
        price_in_per_million=settings.cheap_model_price_in,
        price_out_per_million=settings.cheap_model_price_out,
    )
    cheap_issues = cheap_call.pre_issues + validate_invoice(cheap_call.invoice)
    cheap_confidence = compute_confidence(cheap_call.invoice, cheap_issues, escalated=False)
    attempts = [PipelineAttempt(cheap_call, cheap_issues, cheap_confidence)]

    errors = [i for i in cheap_issues if i.severity == "error"]
    escalated = bool(errors)
    final_attempt = attempts[0]

    if escalated:
        strong_call = extract_invoice(
            client,
            model=settings.strong_model,
            document_text=document_text,
            price_in_per_million=settings.strong_model_price_in,
            price_out_per_million=settings.strong_model_price_out,
            prior_issues=[e.message for e in errors],
        )
        strong_issues = strong_call.pre_issues + validate_invoice(strong_call.invoice)
        strong_confidence = compute_confidence(strong_call.invoice, strong_issues, escalated=True)
        attempts.append(PipelineAttempt(strong_call, strong_issues, strong_confidence))
        final_attempt = attempts[-1]

    status = "done" if final_attempt.confidence >= settings.confidence_threshold else "needs_review"

    return ReviewOutcome(
        invoice=final_attempt.call.invoice,
        issues=final_attempt.issues,
        confidence=final_attempt.confidence,
        model=final_attempt.call.model,
        escalated=escalated,
        status=status,
        cost_usd=sum(a.call.cost_usd for a in attempts),
        latency_ms=sum(a.call.latency_ms for a in attempts),
        attempts=attempts,
    )
