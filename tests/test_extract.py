from app.doctypes.invoice.schema import GROUNDED_FIELDS, Invoice
from app.pipeline.extract import safe_build_invoice, unwrap_grounded_raw


def test_safe_build_invoice_accepts_clean_data():
    raw = {
        "supplier": "Acme Co",
        "invoice_number": "INV-1",
        "invoice_date": "2026-01-01",
        "due_date": "2026-01-31",
        "currency": "USD",
        "subtotal": 100.0,
        "tax": 10.0,
        "total": 110.0,
        "line_items": [],
    }
    invoice, issues = safe_build_invoice(Invoice, raw)
    assert invoice.supplier == "Acme Co"
    assert issues == []


def test_safe_build_invoice_drops_unparseable_date_instead_of_failing():
    raw = {
        "supplier": "Acme Co",
        "invoice_number": "INV-1",
        "invoice_date": "03/04",  # ambiguous European-style partial date
        "due_date": None,
        "currency": "USD",
        "subtotal": 100.0,
        "tax": 10.0,
        "total": 110.0,
        "line_items": [],
    }
    invoice, issues = safe_build_invoice(Invoice, raw)
    assert invoice.invoice_date is None
    assert invoice.supplier == "Acme Co"
    assert any(i.code == "unparseable_field" for i in issues)


def test_safe_build_invoice_drops_multiple_bad_fields_independently():
    raw = {
        "supplier": "Acme Co",
        "invoice_number": "INV-1",
        "invoice_date": "03/04",
        "due_date": "not a date",
        "currency": "USD",
        "subtotal": 100.0,
        "tax": 10.0,
        "total": 110.0,
        "line_items": [],
    }
    invoice, issues = safe_build_invoice(Invoice, raw)
    assert invoice.invoice_date is None
    assert invoice.due_date is None
    assert invoice.supplier == "Acme Co"
    assert invoice.total == 110.0
    assert {i.message.split("'")[1] for i in issues} == {"invoice_date", "due_date"}


def test_safe_build_invoice_drops_bad_line_item_field_only():
    raw = {
        "supplier": "Acme Co",
        "invoice_number": "INV-1",
        "invoice_date": "2026-01-01",
        "due_date": None,
        "currency": "USD",
        "subtotal": 100.0,
        "tax": 10.0,
        "total": 110.0,
        "line_items": [
            {"description": "Widget", "quantity": "a lot", "unit_price": 100.0, "amount": 100.0},
        ],
    }
    invoice, issues = safe_build_invoice(Invoice, raw)
    assert invoice.line_items[0].quantity is None
    assert invoice.line_items[0].description == "Widget"
    assert invoice.line_items[0].amount == 100.0


def test_unwrap_grounded_raw_splits_value_and_hints():
    raw = {
        "supplier": {"value": "Acme Co", "page_no": 1, "source_text": "Acme Co"},
        "total": {"value": 110.0, "page_no": 1, "source_text": "Total: 110.00"},
        "line_items": [{"description": "Widget", "quantity": 1.0, "unit_price": 100.0, "amount": 100.0}],
    }
    plain, hints = unwrap_grounded_raw(raw, GROUNDED_FIELDS)
    assert plain["supplier"] == "Acme Co"
    assert plain["total"] == 110.0
    assert plain["line_items"] == raw["line_items"]  # ungrounded field passes through untouched
    assert hints["supplier"] == {"page_no": 1, "source_text": "Acme Co"}
    assert hints["total"] == {"page_no": 1, "source_text": "Total: 110.00"}


def test_unwrap_grounded_raw_degrades_malformed_wrapper_to_null():
    # Model didn't follow the {value, page_no, source_text} shape for a field -
    # should degrade to null instead of raising.
    raw = {"supplier": "Acme Co (not wrapped)"}
    plain, hints = unwrap_grounded_raw(raw, GROUNDED_FIELDS)
    assert plain["supplier"] is None
    assert hints["supplier"] == {"page_no": None, "source_text": None}
