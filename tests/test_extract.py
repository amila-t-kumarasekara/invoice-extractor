from app.pipeline.extract import safe_build_invoice


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
    invoice, issues = safe_build_invoice(raw)
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
    invoice, issues = safe_build_invoice(raw)
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
    invoice, issues = safe_build_invoice(raw)
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
    invoice, issues = safe_build_invoice(raw)
    assert invoice.line_items[0].quantity is None
    assert invoice.line_items[0].description == "Widget"
    assert invoice.line_items[0].amount == 100.0
