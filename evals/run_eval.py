"""Eval harness.

Mirrors the real pipeline (app/pipeline/orchestrator.py's ExtractStage +
ValidateStage) closely enough to measure accuracy, without needing a live
Postgres/worker: parse (in-memory), extract, ground (a pure function, no DB),
validate business rules, run the independent AI reviewer, escalate. The one
thing it deliberately skips is the cross-tenant-document cross-checks
(duplicate invoice number, known-supplier lookup) - those need a real
database of *other* documents to mean anything, which an isolated eval run
doesn't have.

The `--mode` flag lets you run the same samples through less of the pipeline,
which is how you produce the before/after story ("accuracy went from X to Y
when I added validation + escalation"):

    python -m evals.run_eval --mode cheap-only       # cheap model, no rules, no escalation
    python -m evals.run_eval --mode cheap+validate   # + rule validation (still no escalation)
    python -m evals.run_eval --mode full             # + grounding + reviewer + escalation (default)

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

Use `null` for any field the invoice genuinely doesn't have. A label file with
top-level `"doc_type": "non_invoice"` is skipped by this script - those
samples exist for Phase 5 (classify/route), not for field-accuracy scoring.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from google import genai
from pydantic import BaseModel

from app.core.config import get_settings
from app.doctypes.registry import REGISTRY
from app.models import Issue
from app.pipeline.extract import extract_document
from app.pipeline.grounding import ground_field
from app.pipeline.parse import ParsedDocument, parse_pdf
from app.pipeline.reviewer import review_fields

DOCTYPE = REGISTRY["invoice"]
SAMPLES_DIR = Path(__file__).parent / "samples"
MODES = ("cheap-only", "cheap+validate", "full")


def load_samples(samples_dir: Path) -> list[tuple[Path, dict]]:
    samples = []
    for json_path in sorted(samples_dir.glob("*.json")):
        expected = json.loads(json_path.read_text())
        if expected.get("doc_type") == "non_invoice":
            continue
        pdf_path = json_path.with_suffix(".pdf")
        if not pdf_path.exists():
            print(f"skipping {json_path.name}: no matching {pdf_path.name}")
            continue
        samples.append((pdf_path, expected))
    return samples


def values_match(expected, actual) -> bool:
    if isinstance(actual, date):
        actual = actual.isoformat()
    if expected is None:
        return actual is None
    if isinstance(expected, (int, float)):
        return actual is not None and abs(float(actual) - float(expected)) < 0.01
    return actual is not None and str(actual).strip().lower() == str(expected).strip().lower()


def _page_words(parsed: ParsedDocument) -> dict[int, list[dict]]:
    return {p.page_number: [w.to_json() for w in p.words] for p in parsed.pages}


def run_full(client: genai.Client, settings, parsed: ParsedDocument) -> tuple[BaseModel, list[Issue], bool, float, int]:
    """Mirrors app/pipeline/orchestrator.py's ExtractStage + ValidateStage,
    minus the DB-backed cross-checks. Keep this in sync with that file."""
    text = parsed.as_prompt_text()
    page_words = _page_words(parsed)

    cheap_call = extract_document(
        client,
        doctype=DOCTYPE,
        model=settings.cheap_model,
        document_text=text,
        price_in_per_million=settings.cheap_model_price_in,
        price_out_per_million=settings.cheap_model_price_out,
    )
    rule_issues = DOCTYPE.validate_fn(cheap_call.record)
    all_issues = cheap_call.pre_issues + rule_issues

    values = {f: getattr(cheap_call.record, f, None) for f in DOCTYPE.all_fields}
    source_texts = {f: cheap_call.grounding_hints.get(f, {}).get("source_text") for f in DOCTYPE.all_fields}
    grounding = {
        f: ground_field(page_words, cheap_call.grounding_hints.get(f, {}).get("page_no"), source_texts[f])
        for f in DOCTYPE.all_fields
    }
    review = review_fields(
        client,
        model=settings.cheap_model,
        fields=DOCTYPE.all_fields,
        values=values,
        source_texts=source_texts,
        price_in_per_million=settings.cheap_model_price_in,
        price_out_per_million=settings.cheap_model_price_out,
    )

    errors = [i for i in all_issues if i.severity == "error"]
    ungrounded_required = [f for f in DOCTYPE.required_fields if grounding.get(f) is None and values.get(f) is not None]
    disagreed_required = [f for f in DOCTYPE.required_fields if review.agreements.get(f) is False]

    record = cheap_call.record
    escalated = False
    cost = cheap_call.cost_usd + review.cost_usd
    latency = cheap_call.latency_ms + review.latency_ms

    if errors or ungrounded_required or disagreed_required:
        escalated = True
        reasons = [e.message for e in errors]
        if ungrounded_required:
            reasons.append(f"Could not verify these fields appear in the text as claimed: {', '.join(ungrounded_required)}")
        if disagreed_required:
            reasons.append(f"Independent review flagged these fields as not matching cited source text: {', '.join(disagreed_required)}")

        strong_call = extract_document(
            client,
            doctype=DOCTYPE,
            model=settings.strong_model,
            document_text=text,
            price_in_per_million=settings.strong_model_price_in,
            price_out_per_million=settings.strong_model_price_out,
            prior_issues=reasons,
        )
        record = strong_call.record
        cost += strong_call.cost_usd
        latency += strong_call.latency_ms
        all_issues = strong_call.pre_issues + DOCTYPE.validate_fn(strong_call.record)

    return record, all_issues, escalated, cost, latency


def run_one(client: genai.Client, settings, pdf_path: Path, mode: str) -> tuple[BaseModel, float, int]:
    parsed = parse_pdf(str(pdf_path), min_chars_per_page=settings.ocr_text_layer_min_chars)

    if mode == "full":
        record, _issues, _escalated, cost, latency_ms = run_full(client, settings, parsed)
        return record, cost, latency_ms

    call = extract_document(
        client,
        doctype=DOCTYPE,
        model=settings.cheap_model,
        document_text=parsed.as_prompt_text(),
        price_in_per_million=settings.cheap_model_price_in,
        price_out_per_million=settings.cheap_model_price_out,
    )
    if mode == "cheap+validate":
        DOCTYPE.validate_fn(call.record)  # run for parity with the real pipeline; issues unused in scoring
    return call.record, call.cost_usd, call.latency_ms


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=MODES, default="full")
    parser.add_argument("--samples-dir", type=Path, default=SAMPLES_DIR)
    parser.add_argument("--report", type=Path, default=None, help="also write a Markdown report to this path")
    args = parser.parse_args()

    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key)

    samples = load_samples(args.samples_dir)
    if not samples:
        print(f"No labeled samples found in {args.samples_dir}. Add <name>.pdf + <name>.json pairs.")
        return

    field_correct = {f: 0 for f in DOCTYPE.all_fields}
    total_cost = 0.0
    total_latency = 0
    latencies = []
    rows = []

    print(f"mode={args.mode}  samples={len(samples)}\n")
    for pdf_path, expected in samples:
        record, cost, latency_ms = run_one(client, settings, pdf_path, args.mode)
        total_cost += cost
        total_latency += latency_ms
        latencies.append(latency_ms)

        row_correct = 0
        for f in DOCTYPE.all_fields:
            ok = values_match(expected.get(f), getattr(record, f, None))
            field_correct[f] += int(ok)
            row_correct += int(ok)
        rows.append((pdf_path.stem, row_correct, cost, latency_ms))
        print(f"  {pdf_path.stem:<24} {row_correct}/{len(DOCTYPE.all_fields)} fields correct  "
              f"${cost:.4f}  {latency_ms}ms")

    n = len(samples)
    overall = sum(field_correct.values()) / (n * len(DOCTYPE.all_fields))
    latencies.sort()
    p50 = latencies[int(0.5 * (n - 1))]
    p95 = latencies[int(0.95 * (n - 1))]

    print("\nper-field accuracy:")
    for f in DOCTYPE.all_fields:
        print(f"  {f:<16} {field_correct[f] / n:.0%}")
    print(f"\noverall field accuracy: {overall:.1%}")
    print(f"avg cost/doc:  ${total_cost / n:.4f}")
    print(f"avg latency:   {total_latency / n:.0f}ms  (p50={p50}ms, p95={p95}ms)")

    if args.report:
        lines = [
            f"# Eval report - mode={args.mode}",
            "",
            f"- samples: {n}",
            f"- overall field accuracy: {overall:.1%}",
            f"- avg cost/doc: ${total_cost / n:.4f}",
            f"- avg latency: {total_latency / n:.0f}ms (p50={p50}ms, p95={p95}ms)",
            "",
            "## Per-field accuracy",
            "",
        ]
        lines += [f"- {f}: {field_correct[f] / n:.0%}" for f in DOCTYPE.all_fields]
        lines += ["", "## Per-document", "", "| document | fields correct | cost | latency |", "|---|---|---|---|"]
        lines += [f"| {name} | {c}/{len(DOCTYPE.all_fields)} | ${cost:.4f} | {lat}ms |" for name, c, cost, lat in rows]
        args.report.write_text("\n".join(lines) + "\n")
        print(f"\nwrote report to {args.report}")


if __name__ == "__main__":
    main()
