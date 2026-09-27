"""Deterministic rule checks against an extracted Invoice.

This is the core of the pipeline's trust story: the LLM's output is never
trusted on its own, it's cross-checked against arithmetic and format rules
that don't require another model call. Every failure becomes a recorded
`Issue` that either gets shown to a reviewer or fed back into the model on
escalation (see review.py).

Note on "dates parse": that check happens for free as a side effect of the
`Invoice` schema itself (invoice_date/due_date are typed `date`, not `str`) -
a value that doesn't parse never survives `safe_build_invoice` in extract.py,
and instead shows up as an `unparseable_field` issue from there.
"""
from __future__ import annotations

from app.models import REQUIRED_FIELDS, Invoice, Issue

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
