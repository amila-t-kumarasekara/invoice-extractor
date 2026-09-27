# Eval samples

Drop 10-15 hand-labeled invoices here as `<name>.pdf` + `<name>.json` pairs.

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

Use `null` for fields the invoice genuinely doesn't have (don't guess when labeling -
the eval script scores `null` vs `null` as correct).

Deliberately include the messy cases called out in the README: a scanned/blurry
invoice, a European date format, a currency only implied by the address, an
invoice whose line items don't add up, a multi-page invoice, and a handwritten
note. Those are what make the eval numbers (and the escalation logic) mean
something.

Run with:

```
python -m evals.run_eval --mode full
```
