"""Eval harness.

Runs the pipeline against a hand-labeled set of invoices and prints per-field
accuracy, cost per document, and latency. The `--mode` flag lets you run the
same samples through less of the pipeline, which is how you produce the
before/after story ("accuracy went from X to Y when I added validation +
escalation"):

    python -m evals.run_eval --mode cheap-only       # cheap model, no rules, no escalation
    python -m evals.run_eval --mode cheap+validate   # + rule validation (still no escalation)
    python -m evals.run_eval --mode full             # + escalation to the strong model (default)

Sample format: for every `evals/samples/<name>.pdf` there must be a matching
`evals/samples/<name>.json` with the expected field values, e.g.:

    {
      "supplier": "Acme Co",
      "invoice_number": "INV-1042",
      "invoice_date": "2026-01-15",
      "due_date": "2026-02-14",
      "currency": "USD",
      "subtotal": 1000.0,
      "tax": 80.0,
      "total": 1080.0
    }

Use `null` for any field the invoice genuinely doesn't have.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from google import genai

from app.config import get_settings
from app.models import ALL_FIELDS, Invoice
from app.pipeline.extract import extract_invoice
from app.pipeline.parse import parse_pdf
from app.pipeline.review import run_pipeline
from app.pipeline.validate import validate_invoice

SAMPLES_DIR = Path(__file__).parent / "samples"
MODES = ("cheap-only", "cheap+validate", "full")


def load_samples(samples_dir: Path) -> list[tuple[Path, dict]]:
    samples = []
    for json_path in sorted(samples_dir.glob("*.json")):
        pdf_path = json_path.with_suffix(".pdf")
        if not pdf_path.exists():
            print(f"skipping {json_path.name}: no matching {pdf_path.name}")
            continue
        samples.append((pdf_path, json.loads(json_path.read_text())))
    return samples


def values_match(expected, actual) -> bool:
    if isinstance(actual, date):
        actual = actual.isoformat()
    if expected is None:
        return actual is None
    if isinstance(expected, (int, float)):
        return actual is not None and abs(float(actual) - float(expected)) < 0.01
    return actual is not None and str(actual).strip().lower() == str(expected).strip().lower()


def run_one(client: genai.Client, settings, pdf_path: Path, mode: str) -> tuple[Invoice, float, int]:
    parsed = parse_pdf(str(pdf_path), min_chars_per_page=settings.ocr_text_layer_min_chars)
    text = parsed.as_prompt_text()

    if mode == "full":
        outcome = run_pipeline(client, settings, text)
        return outcome.invoice, outcome.cost_usd, outcome.latency_ms

    call = extract_invoice(
        client,
        model=settings.cheap_model,
        document_text=text,
        price_in_per_million=settings.cheap_model_price_in,
        price_out_per_million=settings.cheap_model_price_out,
    )
    if mode == "cheap+validate":
        validate_invoice(call.invoice)  # run for parity with the real pipeline; issues unused in scoring
    return call.invoice, call.cost_usd, call.latency_ms


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=MODES, default="full")
    parser.add_argument("--samples-dir", type=Path, default=SAMPLES_DIR)
    args = parser.parse_args()

    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key)

    samples = load_samples(args.samples_dir)
    if not samples:
        print(f"No labeled samples found in {args.samples_dir}. Add <name>.pdf + <name>.json pairs.")
        return

    field_correct = {f: 0 for f in ALL_FIELDS}
    total_cost = 0.0
    total_latency = 0

    print(f"mode={args.mode}  samples={len(samples)}\n")
    for pdf_path, expected in samples:
        invoice, cost, latency_ms = run_one(client, settings, pdf_path, args.mode)
        total_cost += cost
        total_latency += latency_ms

        row_correct = 0
        for f in ALL_FIELDS:
            ok = values_match(expected.get(f), getattr(invoice, f))
            field_correct[f] += int(ok)
            row_correct += int(ok)
        print(f"  {pdf_path.stem:<24} {row_correct}/{len(ALL_FIELDS)} fields correct  "
              f"${cost:.4f}  {latency_ms}ms")

    n = len(samples)
    print("\nper-field accuracy:")
    for f in ALL_FIELDS:
        print(f"  {f:<16} {field_correct[f] / n:.0%}")

    overall = sum(field_correct.values()) / (n * len(ALL_FIELDS))
    print(f"\noverall field accuracy: {overall:.1%}")
    print(f"avg cost/doc:  ${total_cost / n:.4f}")
    print(f"avg latency:   {total_latency / n:.0f}ms")


if __name__ == "__main__":
    main()
