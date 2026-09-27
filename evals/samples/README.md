# Eval samples

## What's here now (synthetic)

`generate_synthetic_samples.py` produces a starter set covering the messy
cases this project cares about: a clean invoice, a multi-page invoice, one
whose line items don't add up, a European-style ambiguous date, a currency
only implied by the address, and a scanned-style invoice (rendered as an
image with no text layer, forcing the OCR path - needs Tesseract, so run it
via Docker if your host doesn't have it installed). It also includes two
non-invoice documents for the future classify stage.

Regenerate with:

```
python -m evals.samples.generate_synthetic_samples
```

**These are not a substitute for real invoices.** They're clean, simple,
computer-rendered text - they won't exercise real scanner noise, varied
fonts/layouts, or actual handwriting the way a real invoice would. Treat any
accuracy number from these as a smoke test that the harness works, not as a
real accuracy baseline.

## Adding real invoices

Drop 10-15 hand-labeled real invoices here as `<name>.pdf` + `<name>.json`
pairs (alongside or replacing the synthetic ones - `run_eval.py` picks up
every `.json`/`.pdf` pair in this directory).

The JSON is the ground truth for every field in `app/models.ALL_FIELDS`:

```json
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
```

Use `null` for fields the invoice genuinely doesn't have (don't guess when
labeling - the eval script scores `null` vs `null` as correct). A label file
with top-level `"doc_type": "non_invoice"` is skipped by the accuracy scoring
(see the two `non_invoice_*` samples) - those exist for the future classify
stage, not for field accuracy.

A real handwritten-note case can't be meaningfully faked synthetically - if
you want that story, it needs an actual scanned invoice with a handwritten
note on it.

Run with:

```
python -m evals.run_eval --mode full
python -m evals.run_eval --mode full --report evals/report.md
```
