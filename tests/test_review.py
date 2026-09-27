from datetime import date

from app.doctypes.invoice.schema import ALL_FIELDS, REQUIRED_FIELDS, Invoice
from app.models import Issue
from app.pipeline.review import compute_confidence, score_field

FULL_INVOICE = Invoice(
    supplier="Acme Co",
    invoice_number="INV-1",
    invoice_date=date(2026, 1, 1),
    due_date=date(2026, 1, 31),
    currency="USD",
    subtotal=100.0,
    tax=10.0,
    total=110.0,
)

GROUNDED_ALL = {f: {"page_no": 1, "bbox": [0, 0, 1, 1], "source_text": "x"} for f in ALL_FIELDS}


def test_score_field_perfect_case():
    fs = score_field("total", 110.0, grounded=True, issues=[], reviewer_agrees=True, escalated=False)
    assert fs.score == 1.0


def test_score_field_null_value_is_vacuously_grounded():
    fs = score_field("due_date", None, grounded=False, issues=[], reviewer_agrees=None, escalated=False)
    assert fs.grounded == 1.0
    assert fs.score == 1.0


def test_score_field_ungrounded_nonnull_value_penalized():
    fs = score_field("total", 110.0, grounded=False, issues=[], reviewer_agrees=None, escalated=False)
    assert fs.grounded == 0.0
    assert fs.score == 0.75


def test_score_field_error_issue_on_this_field_penalizes_only_that_field():
    issues = [Issue(code="total_mismatch", message="x", severity="error", field="total")]
    fs_total = score_field("total", 110.0, grounded=True, issues=issues, reviewer_agrees=True, escalated=False)
    fs_other = score_field("supplier", "Acme", grounded=True, issues=issues, reviewer_agrees=True, escalated=False)
    assert fs_total.rules_passed == 0.0
    assert fs_other.rules_passed == 1.0


def test_score_field_warning_issue_does_not_penalize():
    issues = [Issue(code="invalid_currency", message="x", severity="warning", field="currency")]
    fs = score_field("currency", "XYZ", grounded=True, issues=issues, reviewer_agrees=True, escalated=False)
    assert fs.rules_passed == 1.0


def test_score_field_reviewer_disagreement_penalizes():
    fs = score_field("total", 110.0, grounded=True, issues=[], reviewer_agrees=False, escalated=False)
    assert fs.reviewer_agreed == 0.0


def test_score_field_escalation_penalizes_every_field_uniformly():
    fs = score_field("total", 110.0, grounded=True, issues=[], reviewer_agrees=True, escalated=True)
    assert fs.not_escalated == 0.0
    assert fs.score == 0.75


def test_compute_confidence_perfect_invoice_scores_one():
    doc_score, scores = compute_confidence(
        FULL_INVOICE,
        all_fields=ALL_FIELDS,
        required_fields=REQUIRED_FIELDS,
        issues=[],
        grounding=GROUNDED_ALL,
        reviewer_agreements={},
        escalated=False,
    )
    assert doc_score == 1.0
    assert all(s.score == 1.0 for s in scores.values())


def test_compute_confidence_document_score_is_min_over_required_fields():
    issues = [Issue(code="missing_required_field", message="x", severity="error", field="total")]
    doc_score, scores = compute_confidence(
        FULL_INVOICE,
        all_fields=ALL_FIELDS,
        required_fields=REQUIRED_FIELDS,
        issues=issues,
        grounding=GROUNDED_ALL,
        reviewer_agreements={},
        escalated=False,
    )
    # total's rules_passed is dragged to 0 -> its score is 0.75; document score
    # takes the min across required fields, so it should equal total's score,
    # not an average across all of them.
    assert doc_score == scores["total"].score
    assert doc_score < scores["supplier"].score


def test_compute_confidence_ignores_non_required_fields_for_document_score():
    # currency isn't required - tanking it shouldn't affect the document score.
    issues = [Issue(code="invalid_currency", message="x", severity="error", field="currency")]
    doc_score, _ = compute_confidence(
        FULL_INVOICE,
        all_fields=ALL_FIELDS,
        required_fields=REQUIRED_FIELDS,
        issues=issues,
        grounding=GROUNDED_ALL,
        reviewer_agreements={},
        escalated=False,
    )
    assert doc_score == 1.0
