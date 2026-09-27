# Invoice Extractor

A small, opinionated pipeline for turning uploaded invoice PDFs into validated,
structured JSON - built to demonstrate model routing, validation layers,
idempotency, and evaluation, not to be a product.

## Architecture

```
                POST /documents (tenant_id, file)
                          |
                          v
              security.py: scan_pdf_bytes (JS/Launch/EmbeddedFile/...)
                          |
                  suspicious? --yes--> 400, nothing stored, no job
                          | no
                          v
                 sha256(file) + tenant_id
                          |
                 already seen? ---yes---> return existing document
                          | no
                          v
              save file, insert `documents` row (pending)
              insert `jobs` row (queued)
                          |
                          v
   worker: SELECT ... FOR UPDATE SKIP LOCKED  <-----+  (N workers poll concurrently)
                          |                          |
                          v                          |
              parse.py: PDF text layer               |
                  \--(sparse?)--> Tesseract OCR       |
                          |                           |
                          v                           |
              security.py: scan_text (prompt injection)
                          |
                  suspicious? --yes--> `rejected`, no retry, no LLM call
                          | no
                          v
              extract.py: cheap model -> Invoice      |
                          |                            |
                          v                            |
              validate.py: arithmetic + format rules   |
                          |                             |
              issues (errors)? --no--> confidence, status
                          | yes
                          v
              extract.py: strong model + prior issues
                          |
                          v
              validate.py again -> confidence, status
                          |
                          v
              write `extractions` row(s), update `documents.status`
              on exception: attempts++, backoff, retry, or -----+
                                                                  v
                                                        `failed` (dead-letter)
```

## Stack

Python 3.12, FastAPI, Postgres (Docker Compose), SQLAlchemy, Pydantic v2,
PyMuPDF + Tesseract for OCR, Gemini (`gemini-2.5-flash` as the cheap/fast
model, `gemini-2.5-pro` as the strong model, via `google-genai`'s Interactions
API), pytest. A single-page HTML upload form is served at `/` for manual
testing instead of a separate Streamlit app.

## Key design decisions

1. **Idempotency at upload** (`app/api.py`). The unique key is
   `(tenant_id, file_hash)` where `file_hash = sha256(file bytes)`. Re-uploading
   the same file for the same tenant returns the existing document instead of
   enqueuing a second job. A `UNIQUE` constraint backs this up so a race
   between two identical concurrent uploads can't create two documents -
   the loser catches the `IntegrityError` and returns the winner's row. Same
   idea as an idempotency key on a hotel booking POST, just derived from
   content instead of a client-supplied header.

2. **The job queue lives in Postgres** (`app/worker.py`), not Redis or SQS.
   Workers claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`, so any number
   of worker processes can poll the same `jobs` table without double-claiming
   a row and without a separate broker. Retries use exponential backoff
   (`next_attempt_at`); once `attempts >= MAX_JOB_ATTEMPTS` the job and its
   document move to `failed` permanently - a dead-letter state a human has to
   look at. At real scale (many workers, cross-region, backpressure from a
   broker) this table would become SQS or Pub/Sub; for a single Postgres
   instance this is one less moving part.

3. **Parse before the LLM** (`app/pipeline/parse.py`). Every page tries its
   embedded text layer first (free, exact, instant). Only pages where the text
   layer is empty or too sparse fall back to Tesseract OCR. Most
   digitally-generated invoices never need OCR, let alone a vision model, just
   to get text out - "use AI only where it adds value."

4. **Structured extraction** (`app/pipeline/extract.py`). The cheap model
   fills the `Invoice` Pydantic schema (`app/models.py`) via a forced function
   call (`tool_choice: {"allowed_tools": {"mode": "any", ...}}` on Gemini's
   Interactions API), so the response is always well-formed JSON, never prose
   to regex out. The system prompt explicitly tells the model to use `null`
   instead of guessing
   - the single biggest lever against hallucinated totals and dates.
   `safe_build_invoice` also degrades gracefully: if the model returns one
   malformed field (a garbled date is the common case), only that field is
   dropped to `null` and recorded as an `unparseable_field` issue, instead of
   discarding the whole extraction.

5. **Rule validation is the core** (`app/pipeline/validate.py`). Deterministic,
   no model call: line items sum to the subtotal, subtotal + tax = total
   (within a cent-level tolerance), due date isn't before the invoice date,
   currency is a real ISO 4217 code, and required fields are present. Every
   failure becomes a recorded `Issue` with a severity (`error` vs `warning`).

6. **Escalation** (`app/pipeline/review.py`). If validation returns any
   `error`-severity issue, the strong model re-runs with the exact list of
   failures appended to the prompt, so it isn't repeating the cheap model's
   mistake blind. Every `Extraction` row records which model produced it, so
   "which model answered, and what did the first pass get wrong" is always
   answerable from the DB - a small, concrete version of model routing.

7. **Confidence and review** (`app/pipeline/review.py::compute_confidence`).
   `confidence = 0.5 * checks_passed_ratio + 0.3 * fields_filled_ratio + 0.2 * (0 if escalated else 1)`.
   `checks_passed_ratio` is measured against a fixed set of rule categories
   (not "however many issues happened to fire"), so scores are comparable
   across documents. Below `CONFIDENCE_THRESHOLD` (default `0.75`), the
   document's status becomes `needs_review` instead of `done`.

8. **Eval harness** (`evals/run_eval.py`). Runs hand-labeled samples through
   the pipeline and prints per-field accuracy, cost/doc, and latency/doc. The
   `--mode` flag (`cheap-only` / `cheap+validate` / `full`) reruns the same
   samples through progressively more of the pipeline, which is how you get
   the "accuracy went from X% to Y% once validation + escalation were added"
   number - see `evals/samples/README.md`.

9. **Input safety is a keyword denylist, not model judgment**
   (`app/pipeline/security.py`). Deterministic checks, all free, all run
   before any LLM sees the document and before anything untrusted is
   persisted: (a) at upload, raw PDF bytes are scanned for object/action names
   that indicate executable or data-exfiltrating content - `/JavaScript`,
   `/Launch`, `/EmbeddedFile`, `/OpenAction`, `/AA`, `/SubmitForm`,
   `/ImportData`, `/RichMedia`, `/GoToR` - a hit is rejected with `400` before
   the file is written to disk or a job is created; (b) after parsing,
   extracted/OCR'd text is scanned for prompt-injection phrasing ("ignore
   previous instructions", "system prompt", "jailbreak", etc.) *and* classic
   SQL-injection payload shapes (`' OR '1'='1`, `UNION SELECT`, `DROP TABLE`,
   `xp_cmdshell`, `SLEEP(`, ...) - a hit sends the document straight to
   `rejected` with no retry and, critically, no LLM call, so a malicious
   invoice never gets a chance to manipulate the extraction model, run up
   cost, or land in storage as a payload waiting for a future unsafe query.
   Every query in this app already goes through the SQLAlchemy ORM with bound
   parameters, so there's no live SQL-injection path today - the check is
   deliberate defense-in-depth against a future export/reporting feature that
   might not be as careful. Deliberately not an LLM-based classifier: a fixed
   substring list can't itself be talked out of its job by adversarial
   phrasing, is free, and its behavior is fully auditable. The honest
   tradeoff: it's a raw byte scan, so content hidden in a compressed PDF
   object stream could evade layer (a) (a hardened version would decompress
   with pikepdf/qpdf first), and layer (b) only catches known payload shapes,
   not every rewording or encoding of them (e.g. `UNI/**/ON SELECT`).

## Database tables

- `documents`: id, tenant_id, file_hash (unique with tenant_id), filename, storage_path, status, created_at
- `jobs`: id, document_id, status, attempts, locked_at, next_attempt_at, last_error, created_at
- `extractions`: id, document_id, model, data (JSONB), issues (JSONB), confidence, cost_usd, latency_ms, created_at
  - one row per model call (cheap, and strong if escalated) - the latest row for
    a document is the one that determined its final status.

## Folder structure

```
invoice-extractor/
  app/
    api.py          # upload, get result, HTML upload page
    worker.py        # job loop
    pipeline/
      parse.py       # text layer + OCR
      security.py    # keyword/structure denylist - PDF bytes + parsed text
      extract.py     # LLM + Pydantic (forced function call)
      validate.py    # rules
      review.py      # escalation + confidence
    models.py         # Pydantic schemas
    config.py         # settings (env vars)
    db.py              # SQLAlchemy models + session
  evals/
    samples/          # PDFs + expected JSON (bring your own)
    run_eval.py
  tests/
  docker-compose.yml
  Dockerfile
  requirements.txt
  README.md
```

## Running it

```bash
cp .env.example .env
# edit .env: set GEMINI_API_KEY (https://ai.google.dev/gemini-api/docs/api-key)

docker compose up --build
```

- Upload UI: http://localhost:8000/
- Upload via curl:
  ```bash
  curl -F tenant_id=demo -F file=@invoice.pdf http://localhost:8000/documents
  curl http://localhost:8000/documents/<document_id>
  ```

To run outside Docker (e.g. for the eval harness against a local Postgres):

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# macOS: brew install tesseract
docker compose up -d db
python -m app.worker            # in one terminal
uvicorn app.api:app --reload    # in another
```

## Tests

```bash
pytest
```

Covers rule validation (`validate.py`), the field-by-field degradation logic
in `safe_build_invoice` (including the bug where a single malformed date field
was wiping out unrelated fields like `supplier` - fixed by locating the exact
pydantic error path instead of nulling fields in dict order), confidence
scoring, text-layer/OCR parsing, and the security denylist (`security.py`) -
the PDF-structure scan (JavaScript/Launch/EmbeddedFile), the prompt-injection
text scan, and the SQL-injection text scan (including a false-positive guard:
"Union Bank", "Select Corp", and "please execute payment" must *not* trigger
it, since the denylist matches payload shapes like `UNION SELECT` and `EXEC(`,
not bare SQL keywords that show up in normal company names).

I also drove live uploads through `docker compose up`: a clean invoice, one
with a raw `/JavaScript` + `/OpenAction` object, one with clean PDF structure
but "IGNORE PREVIOUS INSTRUCTIONS..." in its text layer, and one with
`Supplier: Acme'; DROP TABLE documents; --` as the supplier field. The JS one
gets `400`ed at upload with nothing written to disk; the prompt-injection and
SQLi ones both get accepted at upload (their PDF structure is clean) but get
flipped to `rejected` by the worker immediately after parsing, with zero LLM
calls made for either (verified against the actual job/document rows in
Postgres).

## Eval harness

```bash
# add labeled PDFs to evals/samples/ first - see evals/samples/README.md
python -m evals.run_eval --mode cheap-only
python -m evals.run_eval --mode cheap+validate
python -m evals.run_eval --mode full
```

## Learnings

_Fill this in against real invoices - these are the interview stories._

- **Scanned/blurry invoice:**
- **European date format (e.g. 03/04):**
- **Currency only implied by the address:**
- **Line items that don't add up:**
- **Multi-page invoice:**
- **Handwritten note on the invoice:**
- **Same file uploaded twice:**
