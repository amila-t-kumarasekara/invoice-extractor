from datetime import date

from app.models import Invoice, LineItem
from app.pipeline.validate import validate_invoice


def make_invoice(**overrides) -> Invoice:
    defaults = dict(
        supplier="Acme Co",
        invoice_number="INV-1",
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 1, 31),
        currency="USD",
        subtotal=100.0,
        tax=10.0,
        total=110.0,
        line_items=[LineItem(description="Widget", quantity=1, unit_price=100.0, amount=100.0)],
    )
    defaults.update(overrides)
    return Invoice(**defaults)


def test_clean_invoice_has_no_issues():
    issues = validate_invoice(make_invoice())
    assert issues == []


def test_missing_required_field_is_flagged():
    invoice = make_invoice(supplier=None)
    issues = validate_invoice(invoice)
    codes = [i.code for i in issues]
    assert "missing_required_field" in codes
    assert issues[codes.index("missing_required_field")].severity == "error"


def test_line_items_not_matching_subtotal_is_flagged():
    invoice = make_invoice(subtotal=999.0)
    issues = validate_invoice(invoice)
    assert any(i.code == "line_items_subtotal_mismatch" for i in issues)


def test_subtotal_plus_tax_not_matching_total_is_flagged():
    invoice = make_invoice(total=500.0)
    issues = validate_invoice(invoice)
    assert any(i.code == "total_mismatch" for i in issues)


def test_small_rounding_difference_is_tolerated():
    invoice = make_invoice(subtotal=100.0, tax=10.004, total=110.0)
    issues = validate_invoice(invoice)
    assert not any(i.code == "total_mismatch" for i in issues)


def test_due_date_before_invoice_date_is_flagged():
    invoice = make_invoice(invoice_date=date(2026, 2, 1), due_date=date(2026, 1, 1))
    issues = validate_invoice(invoice)
    assert any(i.code == "due_date_before_invoice_date" for i in issues)


def test_invalid_currency_is_flagged_as_warning():
    invoice = make_invoice(currency="US Dollars")
    issues = validate_invoice(invoice)
    matching = [i for i in issues if i.code == "invalid_currency"]
    assert len(matching) == 1
    assert matching[0].severity == "warning"


def test_missing_amounts_do_not_crash_the_sum_check():
    invoice = make_invoice(line_items=[LineItem(description="Mystery", amount=None)])
    issues = validate_invoice(invoice)
    assert not any(i.code == "line_items_subtotal_mismatch" for i in issues)
