"""Validation for the invoice doctype - two of the three stages from
NEW_PLAN.md Phase 7 (schema validation is the Pydantic model itself, checked
during `safe_build_invoice` in app/pipeline/extract.py):

1. `validate_invoice` - business rules, deterministic, no DB, no model call:
   line items sum to the subtotal, subtotal + tax = total, due date isn't
   before invoice date, currency is a real ISO code, required fields present.

2. `cross_check_invoice` - cross-document checks against this tenant's other
   data: a duplicate (supplier, invoice_number) pair already seen, and an
   unrecognized supplier against a small seeded `suppliers` table that stands
   in for real master data. Both are warnings, not errors - a duplicate or an
   unfamiliar supplier might be entirely legitimate, so they're surfaced for
   review rather than blocking approval outright.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.db import Document, Extraction, FieldValue, Supplier
from app.doctypes.invoice.schema import REQUIRED_FIELDS, Invoice
from app.models import Issue

AMOUNT_TOLERANCE = 0.02  # absolute currency units; covers cent-level rounding

# Common ISO 4217 codes. Not exhaustive, but covers the currencies this project
# is realistically going to see invoices in; extend as needed.
ISO_4217_CODES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "CNY", "HKD",
    "SGD", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "RON", "BGN", "TRY",
    "INR", "IDR", "MYR", "THB", "PHP", "VND", "KRW", "ZAR", "BRL", "MXN",
    "AED", "SAR", "ILS", "EGP", "NGN", "KES", "PKR", "BDT", "LKR", "RUB",
    "UAH", "ARS", "CLP", "COP", "PEN", "TWD",
}


def validate_invoice(invoice: Invoice) -> list[Issue]:
    issues: list[Issue] = []

    for field_name in REQUIRED_FIELDS:
        if getattr(invoice, field_name) in (None, ""):
            issues.append(
                Issue(
                    code="missing_required_field",
                    message=f"Required field '{field_name}' is missing",
                    severity="error",
                    field=field_name,
                )
            )

    if invoice.line_items:
        amounts = [li.amount for li in invoice.line_items]
        if invoice.subtotal is not None and all(a is not None for a in amounts) and amounts:
            line_sum = sum(amounts)  # type: ignore[arg-type]
            if abs(line_sum - invoice.subtotal) > AMOUNT_TOLERANCE:
                issues.append(
                    Issue(
                        code="line_items_subtotal_mismatch",
                        message=f"Line items sum to {line_sum:.2f} but subtotal is {invoice.subtotal:.2f}",
                        field="subtotal",
                    )
                )

    if invoice.subtotal is not None and invoice.tax is not None and invoice.total is not None:
        expected_total = invoice.subtotal + invoice.tax
        if abs(expected_total - invoice.total) > AMOUNT_TOLERANCE:
            issues.append(
                Issue(
                    code="total_mismatch",
                    message=f"subtotal ({invoice.subtotal:.2f}) + tax ({invoice.tax:.2f}) "
                    f"= {expected_total:.2f} but total is {invoice.total:.2f}",
                    field="total",
                )
            )

    if invoice.invoice_date is not None and invoice.due_date is not None:
        if invoice.due_date < invoice.invoice_date:
            issues.append(
                Issue(
                    code="due_date_before_invoice_date",
                    message=f"Due date {invoice.due_date} is before invoice date {invoice.invoice_date}",
                    field="due_date",
                )
            )

    if invoice.currency is not None and invoice.currency.upper() not in ISO_4217_CODES:
        issues.append(
            Issue(
                code="invalid_currency",
                message=f"'{invoice.currency}' is not a recognized ISO 4217 currency code",
                severity="warning",
                field="currency",
            )
        )

    return issues


def _find_duplicate_invoice(db: Session, tenant_id: str, document_id: str, supplier: str, invoice_number: str) -> str | None:
    """Returns the id of another document in this tenant whose latest
    extraction has the same (supplier, invoice_number), or None.

    Deliberately compares in Python rather than pushing string comparison
    into a JSONB SQL expression - the result set here is small (one row pair
    per candidate document), and it avoids fragile `->>`/`astext` casting for
    a demo-scale project. Would need revisiting at real scale.
    """
    latest_attempt_subq = (
        db.query(Extraction.document_id, func.max(Extraction.attempt_no).label("max_attempt"))
        .join(Document, Document.id == Extraction.document_id)
        .filter(Document.tenant_id == tenant_id, Document.id != document_id)
        .group_by(Extraction.document_id)
        .subquery()
    )
    latest_extractions = (
        db.query(Extraction.id, Extraction.document_id)
        .join(
            latest_attempt_subq,
            (Extraction.document_id == latest_attempt_subq.c.document_id)
            & (Extraction.attempt_no == latest_attempt_subq.c.max_attempt),
        )
        .all()
    )
    if not latest_extractions:
        return None

    extraction_id_to_doc = {row.id: row.document_id for row in latest_extractions}
    field_rows = (
        db.query(FieldValue.extraction_id, FieldValue.field, FieldValue.value)
        .filter(FieldValue.extraction_id.in_(extraction_id_to_doc.keys()))
        .filter(FieldValue.field.in_(["supplier", "invoice_number"]))
        .all()
    )

    by_extraction: dict[str, dict[str, str]] = {}
    for extraction_id, field_name, value in field_rows:
        by_extraction.setdefault(extraction_id, {})[field_name] = value

    target_supplier = supplier.strip().lower()
    target_number = invoice_number.strip().lower()
    for extraction_id, fields in by_extraction.items():
        other_supplier = (fields.get("supplier") or "").strip().lower()
        other_number = (fields.get("invoice_number") or "").strip().lower()
        if other_supplier == target_supplier and other_number == target_number:
            return extraction_id_to_doc[extraction_id]
    return None


def _is_known_supplier(db: Session, tenant_id: str, supplier: str) -> bool:
    normalized = supplier.strip().lower()
    match = db.query(Supplier).filter(Supplier.tenant_id == tenant_id).filter(func.lower(Supplier.name) == normalized).first()
    return match is not None


def cross_check_invoice(db: Session, tenant_id: str, document_id: str, invoice: Invoice) -> list[Issue]:
    issues: list[Issue] = []

    if invoice.supplier and invoice.invoice_number:
        duplicate_doc_id = _find_duplicate_invoice(db, tenant_id, document_id, invoice.supplier, invoice.invoice_number)
        if duplicate_doc_id:
            issues.append(
                Issue(
                    code="duplicate_invoice_number",
                    message=f"Invoice number '{invoice.invoice_number}' from supplier '{invoice.supplier}' "
                    f"already exists on document {duplicate_doc_id}",
                    severity="warning",
                    field="invoice_number",
                )
            )

    if invoice.supplier and not _is_known_supplier(db, tenant_id, invoice.supplier):
        issues.append(
            Issue(
                code="unknown_supplier",
                message=f"Supplier '{invoice.supplier}' is not in the known-suppliers list for this tenant",
                severity="warning",
                field="supplier",
            )
        )

    return issues
