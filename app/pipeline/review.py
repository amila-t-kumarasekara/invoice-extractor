"""Confidence scoring.

The escalation loop itself (cheap model -> validate -> strong model on error)
now lives in `app/pipeline/orchestrator.py`'s ExtractStage/ValidateStage, since
each attempt needs to be persisted as its own `extractions` row before the
next stage runs (that's what makes a crashed worker resumable without
re-paying for an LLM call it already made). This module keeps only the pure
scoring function, which stays a plain function of (invoice, issues, escalated)
so it's trivial to unit test in isolation.
"""
from __future__ import annotations

from app.models import ALL_FIELDS, Invoice, Issue

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


def compute_confidence(invoice: Invoice, issues: list[Issue], escalated: bool) -> float:
    errors = {i.code for i in issues if i.severity == "error"} & set(CHECK_CODES)
    checks_passed_ratio = 1 - (len(errors) / len(CHECK_CODES))

    filled = sum(1 for f in ALL_FIELDS if getattr(invoice, f) is not None)
    fields_filled_ratio = filled / len(ALL_FIELDS)

    escalation_score = 0.0 if escalated else 1.0

    confidence = 0.5 * checks_passed_ratio + 0.3 * fields_filled_ratio + 0.2 * escalation_score
    return round(max(0.0, min(1.0, confidence)), 4)
