"""Pydantic schemas for extracted invoice data.

These are deliberately all-optional (except line item description/amount) so the
LLM can return `null` for anything it can't find instead of guessing - guessing
is what causes hallucinated totals and dates.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None


class Invoice(BaseModel):
    supplier: Optional[str] = Field(None, description="Name of the vendor/supplier issuing the invoice")
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    currency: Optional[str] = Field(None, description="ISO 4217 currency code, e.g. USD, EUR, GBP")
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None
    line_items: list[LineItem] = Field(default_factory=list)


# Fields that must be present for a document to be considered fully extracted.
REQUIRED_FIELDS: tuple[str, ...] = (
    "supplier",
    "invoice_number",
    "invoice_date",
    "total",
)

ALL_FIELDS: tuple[str, ...] = (
    "supplier",
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "subtotal",
    "tax",
    "total",
)


class Issue(BaseModel):
    code: str
    message: str
    severity: str = "error"  # "error" | "warning"
    field: Optional[str] = None  # which Invoice field this issue is about, if any
