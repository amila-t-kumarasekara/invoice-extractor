from datetime import date

from app.models import Invoice, Issue
from app.pipeline.review import compute_confidence


def full_invoice() -> Invoice:
    return Invoice(
        supplier="Acme Co",
        invoice_number="INV-1",
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 1, 31),
        currency="USD",
        subtotal=100.0,
        tax=10.0,
        total=110.0,
    )


def test_clean_non_escalated_invoice_scores_highest():
    score = compute_confidence(full_invoice(), issues=[], escalated=False)
    assert score == 1.0


def test_escalation_lowers_confidence_even_when_clean():
    score = compute_confidence(full_invoice(), issues=[], escalated=True)
    assert 0.0 < score < 1.0


def test_only_error_severity_issues_reduce_the_checks_passed_component():
    # Warnings (e.g. an unrecognized currency code) are surfaced to reviewers
    # but don't by themselves push a document into needs_review the way a
    # failed arithmetic check does.
    error_score = compute_confidence(
        full_invoice(), issues=[Issue(code="total_mismatch", message="x", severity="error")], escalated=False
    )
    warning_score = compute_confidence(
        full_invoice(), issues=[Issue(code="invalid_currency", message="x", severity="warning")], escalated=False
    )
    assert error_score < warning_score == 1.0


def test_missing_fields_lower_confidence():
    sparse = Invoice(supplier="Acme Co")
    score = compute_confidence(sparse, issues=[], escalated=False)
    assert score < 1.0
