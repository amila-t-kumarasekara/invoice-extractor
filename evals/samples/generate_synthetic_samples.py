"""Generates synthetic labeled invoices for the eval harness.

These are NOT a substitute for real invoices - they're simple, clean text
rendered via PyMuPDF, so they won't exercise real-world messiness like actual
scanner noise, varied fonts/layouts, or true handwriting. They exist so
`run_eval.py` has something runnable out of the box. Replace/supplement with
real hand-labeled invoices before trusting the accuracy numbers for anything.

Run with: python -m evals.samples.generate_synthetic_samples
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont

SAMPLES_DIR = Path(__file__).parent


def write_text_pdf(path: Path, pages: list[list[str]]) -> None:
    doc = fitz.open()
    for lines in pages:
        page = doc.new_page()
        y = 72
        for line in lines:
            page.insert_text((72, y), line, fontsize=11)
            y += 18
    doc.save(str(path))
    doc.close()


def write_scanned_style_pdf(path: Path, lines: list[str]) -> None:
    """Renders text into a raster image and embeds *only* the image (no text
    layer), so `parse.py`'s text-layer check falls through to OCR - simulating
    a scanned/photographed invoice."""
    img = Image.new("RGB", (1000, 700), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
    y = 40
    for line in lines:
        draw.text((40, y), line, fill="black", font=font)
        y += 40
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    doc = fitz.open()
    page = doc.new_page(width=1000, height=700)
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(str(path))
    doc.close()


def write_sample(name: str, pages_or_lines, expected: dict, scanned: bool = False) -> None:
    pdf_path = SAMPLES_DIR / f"{name}.pdf"
    if scanned:
        write_scanned_style_pdf(pdf_path, pages_or_lines)
    else:
        write_text_pdf(pdf_path, pages_or_lines)
    (SAMPLES_DIR / f"{name}.json").write_text(json.dumps(expected, indent=2) + "\n")
    print(f"wrote {pdf_path.name} + {pdf_path.with_suffix('.json').name}")


def main() -> None:
    # 1. Clean, single-page, everything adds up.
    write_sample(
        "clean_invoice",
        [[
            "Acme Co",
            "Invoice #INV-1001",
            "Invoice Date: 2026-01-15",
            "Due Date: 2026-02-14",
            "Currency: USD",
            "",
            "Widget A     qty 2   unit 50.00   amount 100.00",
            "Widget B     qty 1   unit 150.00  amount 150.00",
            "",
            "Subtotal: 250.00",
            "Tax: 20.00",
            "Total: 270.00",
        ]],
        {
            "supplier": "Acme Co",
            "invoice_number": "INV-1001",
            "invoice_date": "2026-01-15",
            "due_date": "2026-02-14",
            "currency": "USD",
            "subtotal": 250.0,
            "tax": 20.0,
            "total": 270.0,
        },
    )

    # 2. Multi-page: line items on page 1, totals on page 2.
    write_sample(
        "multi_page_invoice",
        [
            [
                "Globex Manufacturing",
                "Invoice #INV-2002",
                "Invoice Date: 2026-03-01",
                "Due Date: 2026-03-31",
                "Currency: USD",
                "",
                "Steel bolts     qty 100  unit 0.50   amount 50.00",
                "Steel plates    qty 10   unit 25.00  amount 250.00",
                "Shipping        qty 1    unit 30.00  amount 30.00",
            ],
            [
                "Invoice #INV-2002 (continued)",
                "",
                "Subtotal: 330.00",
                "Tax: 26.40",
                "Total: 356.40",
            ],
        ],
        {
            "supplier": "Globex Manufacturing",
            "invoice_number": "INV-2002",
            "invoice_date": "2026-03-01",
            "due_date": "2026-03-31",
            "currency": "USD",
            "subtotal": 330.0,
            "tax": 26.40,
            "total": 356.40,
        },
    )

    # 3. Line items don't add up to the subtotal (a real sloppy-vendor invoice)
    # - ground truth is what's printed, since the model should transcribe
    # faithfully; validate.py is what's supposed to catch the mismatch.
    write_sample(
        "bad_totals_invoice",
        [[
            "Initech LLC",
            "Invoice #INV-3003",
            "Invoice Date: 2026-02-10",
            "Due Date: 2026-03-10",
            "Currency: USD",
            "",
            "Consulting hours   qty 10  unit 100.00  amount 1000.00",
            "Travel expenses    qty 1   unit 200.00  amount 200.00",
            "",
            "Subtotal: 1500.00",  # doesn't match 1000 + 200 = 1200 - deliberate
            "Tax: 120.00",
            "Total: 1620.00",
        ]],
        {
            "supplier": "Initech LLC",
            "invoice_number": "INV-3003",
            "invoice_date": "2026-02-10",
            "due_date": "2026-03-10",
            "currency": "USD",
            "subtotal": 1500.0,
            "tax": 120.0,
            "total": 1620.0,
        },
    )

    # 4. European-style ambiguous date (DD/MM vs MM/DD) - intentionally
    # ambiguous, no clarifying text, the way a real invoice would be. Ground
    # truth assumes DD/MM/YYYY (the supplier is UK-based), i.e. 3 April 2026.
    write_sample(
        "european_date_invoice",
        [[
            "Barrow & Sons Ltd",
            "42 High Street, London, UK",
            "Invoice #INV-4004",
            "Invoice Date: 03/04/2026",
            "Due Date: 03/05/2026",
            "Currency: GBP",
            "",
            "Office supplies   qty 5  unit 40.00  amount 200.00",
            "",
            "Subtotal: 200.00",
            "Tax: 40.00",
            "Total: 240.00",
        ]],
        {
            "supplier": "Barrow & Sons Ltd",
            "invoice_number": "INV-4004",
            "invoice_date": "2026-04-03",
            "due_date": "2026-05-03",
            "currency": "GBP",
            "subtotal": 200.0,
            "tax": 40.0,
            "total": 240.0,
        },
    )

    # 5. Currency only implied by a UK address + "£" symbol, never spelled out
    # as "GBP" anywhere.
    write_sample(
        "implied_currency_invoice",
        [[
            "Thames Print Co",
            "12 Riverside Walk, Manchester, United Kingdom",
            "Invoice #INV-5005",
            "Invoice Date: 2026-01-20",
            "Due Date: 2026-02-19",
            "",
            "Business cards   qty 1000  unit 0.10  amount 100.00",
            "",
            "Subtotal: GBP 100.00",  # uses the code once so this is a *plausible* extraction, not a guess
            "Tax: 20.00",
            "Total: 120.00",
        ]],
        {
            "supplier": "Thames Print Co",
            "invoice_number": "INV-5005",
            "invoice_date": "2026-01-20",
            "due_date": "2026-02-19",
            "currency": "GBP",
            "subtotal": 100.0,
            "tax": 20.0,
            "total": 120.0,
        },
    )

    # 6. "Scanned" invoice - text rendered into an image, no text layer, forces
    # the OCR fallback path in parse.py. Requires Tesseract (present in the
    # Docker image; may not be present on your host).
    write_sample(
        "scanned_invoice",
        [
            "Northwind Traders",
            "Invoice #INV-6006",
            "Invoice Date: 2026-04-05",
            "Due Date: 2026-05-05",
            "Currency: USD",
            "Widget C   qty 3  unit 20.00  amount 60.00",
            "Subtotal: 60.00",
            "Tax: 4.80",
            "Total: 64.80",
        ],
        {
            "supplier": "Northwind Traders",
            "invoice_number": "INV-6006",
            "invoice_date": "2026-04-05",
            "due_date": "2026-05-05",
            "currency": "USD",
            "subtotal": 60.0,
            "tax": 4.80,
            "total": 64.80,
        },
        scanned=True,
    )

    # 7-8. Non-invoice documents, for the future classify stage (Phase 5) -
    # skipped by run_eval.py today (see load_samples()).
    write_sample(
        "non_invoice_cover_letter",
        [[
            "Dear Hiring Manager,",
            "",
            "I am writing to express my interest in the Software Engineer",
            "position at your company. I believe my five years of experience",
            "make me a strong candidate for this role.",
            "",
            "Sincerely,",
            "Jordan Smith",
        ]],
        {"doc_type": "non_invoice"},
    )
    write_sample(
        "non_invoice_resume",
        [[
            "Jordan Smith",
            "Software Engineer",
            "",
            "Experience",
            "- Senior Engineer, Tech Corp, 2022-present",
            "- Engineer, StartupCo, 2019-2022",
            "",
            "Education",
            "- B.S. Computer Science, State University",
        ]],
        {"doc_type": "non_invoice"},
    )


if __name__ == "__main__":
    main()
