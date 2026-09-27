"""Per-field confidence gate (NEW_PLAN.md Phase 9).

Each field gets a score in [0, 1] combining four equally-weighted signals:

- `grounded`: was the field's value actually found on the page it claimed?
  Vacuously 1.0 if the value is null - there's nothing to ground, so nothing
  to penalize.
- `rules_passed`: did this specific field trigger an *error*-severity
  validation issue (business rule or cross-check)? Warnings don't count
  against it - an unfamiliar supplier or a duplicate invoice number might be
  entirely legitimate.
- `reviewer_agreed`: did the independent AI reviewer pass (app/pipeline/reviewer.py)
  confirm the value matches its cited source text? Vacuously 1.0 if null
  (nothing to review, or the reviewer step wasn't run).
- `not_escalated`: 1.0 if the cheap model's answer shipped as-is, 0.0 if it
  took a strong-model escalation to get here. Same value for every field in a
  document - escalation is a whole-attempt decision, not a per-field one.

The document's score is the *minimum* across its required fields (NEW_PLAN.md:
"Document score is the lowest score among its required fields") - the least
confident required field caps the whole document. Below
`CONFIDENCE_THRESHOLD`, status becomes `needs_review` instead of `approved`.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from app.models import Issue


@dataclass
class FieldScore:
    field: str
    grounded: float
    rules_passed: float
    reviewer_agreed: float
    not_escalated: float

    @property
    def score(self) -> float:
        return round((self.grounded + self.rules_passed + self.reviewer_agreed + self.not_escalated) / 4, 4)


def score_field(
    field: str,
    value: object,
    *,
    grounded: bool,
    issues: list[Issue],
    reviewer_agrees: bool | None,
    escalated: bool,
) -> FieldScore:
    grounded_score = 1.0 if value is None else (1.0 if grounded else 0.0)
    rules_passed_score = 0.0 if any(i.field == field and i.severity == "error" for i in issues) else 1.0
    reviewer_score = 1.0 if reviewer_agrees in (None, True) else 0.0
    not_escalated_score = 0.0 if escalated else 1.0
    return FieldScore(field, grounded_score, rules_passed_score, reviewer_score, not_escalated_score)


def compute_confidence(
    record: BaseModel,
    *,
    all_fields: tuple[str, ...],
    required_fields: tuple[str, ...],
    issues: list[Issue],
    grounding: dict[str, dict | None],
    reviewer_agreements: dict[str, bool | None],
    escalated: bool,
) -> tuple[float, dict[str, FieldScore]]:
    """Returns (document_score, {field: FieldScore}) - scores are computed for
    every field in `all_fields`, but the document score only aggregates
    `required_fields`."""
    scores: dict[str, FieldScore] = {}
    for field in all_fields:
        scores[field] = score_field(
            field,
            getattr(record, field, None),
            grounded=grounding.get(field) is not None,
            issues=issues,
            reviewer_agrees=reviewer_agreements.get(field),
            escalated=escalated,
        )
    document_score = min((scores[f].score for f in required_fields), default=0.0)
    return round(document_score, 4), scores
