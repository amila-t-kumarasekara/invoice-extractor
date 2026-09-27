# Invoice Extractor

A pipeline for turning uploaded invoice PDFs into validated, structured JSON -
built to demonstrate model routing, validation layers, idempotency, and
evaluation. Originally a single-pass prototype; now rebuilt as a stage-based,
resumable job pipeline per `NEW_PLAN.md` (Phases 0-3 of that plan are done -
see [Status against NEW_PLAN.md](#status-against-new_planmd) at the bottom).

## Architecture

```
                    POST /documents (tenant_id, file)
                              |
                              v
          safety.py: scan_upload (real content-type sniff, size/page
          limits, encrypted/corrupt PDF, JS/Launch/EmbeddedFile denylist)
                              |
                  unsafe? --yes--> 400, nothing stored, no job
                              | no
                              v
                sha256(file) + tenant_id already seen?
                  yes --> return existing document
                              | no
                              v
        Storage.save() + insert `documents` (uploaded) + `jobs` (stage=parse)
                              |
                              v
   worker: SELECT ... FOR UPDATE SKIP LOCKED   <---------------------+
   (also reclaims jobs stuck `processing` past a stale-lock timeout) |
                              |                                       |
                              v                                       |
                 orchestrator.run_stage(job.stage)                    |
                              |                                       |
        +---------------------+---------------------+                |
        v                     v                     v                |
   ParseStage            ExtractStage          ValidateStage          |
   text layer /          cheap model ->        re-validate cheap      |
   Tesseract OCR         Invoice schema        attempt; if any rule   |
   -> safety.scan_text   (forced function      *error*, escalate to   |
   (prompt-injection /   call), persist an     strong model w/ the    |
   SQLi in the text)     `extractions` row     exact errors, persist  |
   -> persist `pages`    + `field_values`      2nd `extractions` row  |
        |                     |                -> compute_confidence |
        |                     |                -> approved |         |
        |                     |                   needs_review       |
        v                     v                     v                |
   status=parsed      status=extracted   status=approved|needs_review|
        |                     |                     |                |
        +----- enqueue next stage's job ------------+                |
                                                                       |
   any stage raises SecurityRejection --> status=rejected, no retry --+
   any other exception --> attempts++, backoff, retry, or -> `failed` (dead-letter)
```

Each stage commits its own DB writes atomically and reads its input from the
DB (not from in-memory state), which is what makes a stage safe to re-run: if
the worker process dies mid-stage, nothing from that half-finished attempt
was ever committed, and a restarted worker's stale-lock reclaim picks the job
back up. Stages that already completed (e.g. `parse`, or a cheap-model call
that already succeeded) are never redone. See
`app/pipeline/orchestrator.py`'s docstring for the honest tradeoff (stage-level
resumability, not exactly-once at the LLM-call level).

## Stack

Python 3.12, FastAPI, Postgres + Alembic migrations (Docker Compose),
SQLAlchemy, Pydantic v2, PyMuPDF + Tesseract for OCR, `filetype` for
content-based MIME sniffing, Gemini (`gemini-2.5-flash` cheap/fast,
`gemini-2.5-pro` strong, via `google-genai`'s Interactions API), pytest. A
single-page HTML upload form is served at `/` for manual testing.

## Key design decisions

1. **Idempotency at upload** (`app/api/upload.py`). The unique key is
   `(tenant_id, file_hash)` where `file_hash = sha256(file bytes)`. Re-uploading
   the same file for the same tenant returns the existing document instead of
   enqueuing a second job. A `UNIQUE` constraint backs this up so a race
   between two identical concurrent uploads can't create two documents - the
   loser catches the `IntegrityError` and returns the winner's row.

2. **Upload-time file safety** (`app/pipeline/safety.py::scan_upload`). The
   *real* file type is sniffed from content (magic bytes via `filetype`), not
   the filename extension - a `.pdf`-named `.exe` is rejected regardless of
   what `Content-Type` the client claims. Also enforced: a size limit, a
   page-count limit, and rejection of encrypted or corrupt/unopenable PDFs.
   All of this runs before the file is written to storage or a job exists.

3. **The job queue lives in Postgres** (`app/queue/`), not Redis or SQS.
   Workers claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`
   (`app/queue/jobs.py::claim_job`), so any number of worker processes can
   poll the same table without double-claiming a row. A job stuck `processing`
   past `STALE_LOCK_MINUTES` is treated as abandoned (its owner presumably
   crashed) and becomes reclaimable again - this was verified live: a job was
   forced into a stale `processing` state and a running worker picked it back
   up and continued retrying it. Ordinary failures retry with exponential
   backoff; once `attempts >= MAX_JOB_ATTEMPTS` the job and document move to
   `failed` permanently (dead-letter).

4. **The pipeline is staged, not one function** (`app/pipeline/orchestrator.py`).
   `parse -> extract -> validate` are three separate jobs against the same
   document, each committing its own output (`pages`, then `extractions`, then
   `validation_issues`/confidence) before enqueuing the next stage. This is
   the resumability story: if a document's `extract` stage keeps failing
   (verified live against a bad API key, retried 4 times with backoff), the
   already-completed `parse` stage's output is never recomputed - confirmed by
   checking the `pages` table stayed at exactly one row per page across every
   retry, not duplicated or reprocessed.

5. **Storage behind an interface** (`app/core/storage.py`). `Storage.save()`
   returns an opaque `storage_key`; only `LocalDiskStorage` knows it's actually
   a path on disk. Swapping in S3/MinIO later means implementing the same
   three methods, not touching pipeline code.

6. **Parse before the LLM** (`app/pipeline/parse.py`). Every page tries its
   embedded text layer first (free, exact, instant). Only pages where the text
   layer is empty or too sparse fall back to Tesseract OCR - verified against
   a synthetic "scanned" invoice (text rendered into an image, no text layer)
   which correctly triggered OCR and produced realistic OCR noise
   ("Northwind Traders" -> "Nerthiwined Traders").

7. **Structured extraction** (`app/pipeline/extract.py`). The cheap model
   fills the `Invoice` Pydantic schema via a forced function call
   (`tool_choice: {"allowed_tools": {"mode": "any", ...}}`), so the response
   is always well-formed JSON. The system prompt explicitly tells the model to
   use `null` instead of guessing. `safe_build_invoice` degrades gracefully:
   if the model returns one malformed field, only that field is dropped to
   `null` and recorded as an `unparseable_field` issue, instead of discarding
   the whole extraction.

8. **Rule validation is the core** (`app/pipeline/validate.py`). Deterministic,
   no model call: line items sum to the subtotal, subtotal + tax = total
   (cent-level tolerance), due date isn't before the invoice date, currency is
   a real ISO 4217 code, required fields are present. Every failure becomes a
   recorded `Issue` (with the specific `field` it's about) with a severity
   (`error` vs `warning`).

9. **Escalation** (`app/pipeline/orchestrator.py::ValidateStage`). If
   validation returns any `error`-severity issue, the strong model re-runs
   with the exact list of failures appended to the prompt. Every `extractions`
   row records which model and attempt number produced it, so "which model
   answered, and what did the first pass get wrong" is answerable from the DB.

10. **Confidence and review** (`app/pipeline/review.py::compute_confidence`).
    `confidence = 0.5 * checks_passed_ratio + 0.3 * fields_filled_ratio + 0.2 * (0 if escalated else 1)`,
    measured against a fixed set of rule categories so scores are comparable
    across documents. Below `CONFIDENCE_THRESHOLD` (default `0.75`), status
    becomes `needs_review` instead of `approved`.

11. **Eval harness** (`evals/run_eval.py`). Runs hand-labeled samples through
    the pipeline and prints per-field accuracy, cost/doc, latency/doc. The
    `--mode` flag (`cheap-only` / `cheap+validate` / `full`) reruns the same
    samples through progressively more of the pipeline, which is how you get
    the "accuracy went from X% to Y%" story - see `evals/samples/README.md`.

12. **Input safety is a keyword/structure denylist, not model judgment**
    (`app/pipeline/safety.py`). Beyond the upload-time file checks (#2 above):
    parsed text is scanned for prompt-injection phrasing ("ignore previous
    instructions", "system prompt", "jailbreak", ...) *and* classic
    SQL-injection payload shapes (`' OR '1'='1`, `UNION SELECT`, `DROP TABLE`,
    `xp_cmdshell`, `SLEEP(`, ...) - a hit sends the document straight to
    `rejected` with no retry and no LLM call. Every DB query already goes
    through the SQLAlchemy ORM with bound parameters, so the SQLi check is
    deliberate defense-in-depth against a future export/reporting feature, not
    a fix for a live vulnerability. Deliberately not an LLM classifier: a
    fixed list can't be talked out of its job by adversarial phrasing, is
    free, and its behavior is fully auditable. Honest tradeoff: the PDF scan
    is raw bytes, so content hidden in a compressed object stream could evade
    it (a hardened version would decompress via pikepdf/qpdf first); the text
    denylists catch known payload shapes, not every rewording/encoding.

## Database tables

- `documents`: id, tenant_id, file_hash (unique w/ tenant_id), filename, mime_type, storage_key, doc_type, status, confidence, created_at, updated_at
- `pages`: id, document_id, page_no, text_source (text_layer/ocr), text, image_key (Phase 4), words (Phase 6, grounding)
- `jobs`: id, document_id, stage (parse/extract/validate), status, attempts, locked_at, next_run_at, last_error
- `extractions`: id, document_id, model, attempt_no, raw (JSONB), tokens (JSONB), cost_usd, latency_ms
  - one row per model call (cheap, and strong if escalated)
- `field_values`: id, extraction_id, field, value, page_no/bbox/source_text/confidence (all null until Phase 6 grounding)
- `validation_issues`: id, extraction_id, field, rule, severity, message
- `corrections`: unused until Phase 10 (review UI) - exists now so the schema doesn't need another migration later

`documents.status` state machine:
`uploaded -> parsed -> extracted -> validated -> approved | needs_review | failed | rejected`
(`classified` is defined in the enum for Phase 5 but nothing sets it yet - every
document is currently assumed to be an invoice, `doc_type="invoice"` hardcoded
at upload.)

## Folder structure

```
invoice-extractor/
  app/
    api/
      upload.py       # POST /documents - safety checks, storage, idempotency
      documents.py    # GET /documents/{id}
      review.py       # GET /review - documents needing a human look
      ui.py            # HTML upload page at /
    core/
      config.py        # Settings (env vars)
      db.py             # SQLAlchemy models + session
      storage.py         # Storage interface + LocalDiskStorage
      logging.py
    queue/
      jobs.py           # claim_job (SKIP LOCKED + stale-lock reclaim), backoff
      worker.py          # poll loop
    pipeline/
      safety.py          # file-type/size/encryption + keyword denylist checks
      parse.py            # text layer + OCR
      extract.py           # LLM + Pydantic (forced function call)
      validate.py           # rules
      review.py              # compute_confidence
      orchestrator.py         # Stage protocol + parse/extract/validate stages
    models.py                  # Invoice/LineItem/Issue Pydantic schemas
  evals/
    samples/                    # PDFs + expected JSON (synthetic starter set included)
    run_eval.py
    report.md
  migrations/                    # Alembic
  tests/
  docker-compose.yml
  Dockerfile
  alembic.ini
  requirements.txt
  NEW_PLAN.md                     # the full 11-phase plan this is being built against
  README.md
```

## Running it

```bash
cp .env.example .env
# edit .env: set GEMINI_API_KEY (https://ai.google.dev/gemini-api/docs/api-key)

docker compose up --build
```

This brings up `db`, then a one-shot `migrate` service (`alembic upgrade head`),
then `api` and `worker` once migrations succeed. (Earlier, `api` and `worker`
both independently called `Base.metadata.create_all()` on startup, and one
lost a duplicate-key race against the other when both started at once - the
`migrate` service exists specifically to make schema creation a single,
ordered step instead of two services racing each other.)

- Upload UI: http://localhost:8000/
- Upload via curl:
  ```bash
  curl -F tenant_id=demo -F file=@invoice.pdf http://localhost:8000/documents
  curl http://localhost:8000/documents/<document_id>
  curl http://localhost:8000/review   # documents needing a human look
  ```

To run outside Docker:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# macOS: brew install tesseract
docker compose up -d db
alembic upgrade head
python -m app.queue.worker      # in one terminal
uvicorn app.api:app --reload    # in another
```

## Tests

```bash
pytest
```

44 tests covering rule validation, the field-by-field degradation logic in
`safe_build_invoice`, confidence scoring, text-layer/OCR parsing, the full
safety denylist (PDF structure, prompt-injection, SQL-injection - including a
false-positive guard so "Union Bank Select Corp" doesn't trigger the SQLi
scan), and the eval harness's scoring helpers.

Beyond the automated suite, the following were verified live against a real
`docker compose up` stack (not just unit-tested), because they depend on
Postgres locking/timing behavior that doesn't fit a unit test:

- **Idempotency**: uploading the same file twice returns the same document ID.
- **Upload safety**: a `.exe` renamed `.pdf` is rejected by content sniffing; a
  PDF with `/JavaScript`+`/OpenAction` is rejected at upload with nothing
  written to disk; a PDF with a prompt-injection payload in its text is
  accepted at upload (clean structure) but rejected by the worker right after
  parsing, with zero LLM calls made; same for a `'; DROP TABLE documents; --`
  payload in a text field.
- **Stage resumability**: after `parse` completed and committed, the `extract`
  stage was made to fail repeatedly (bad API key) and retry with backoff - the
  `pages` table never gained duplicate rows, proving the completed stage was
  never redone.
- **Crash recovery**: a job was forced into a stale `processing` state (as if
  its worker had died mid-stage); a live worker's next poll reclaimed it
  (`attempts` incremented, `locked_at` refreshed) and continued retrying it
  normally.
- **The startup race** described above (`api`/`worker` both calling
  `create_all()` concurrently) was caught this way, then fixed with the
  `migrate` service.

## Eval harness

```bash
python -m evals.samples.generate_synthetic_samples   # already run; regenerate anytime
python -m evals.run_eval --mode cheap-only
python -m evals.run_eval --mode cheap+validate
python -m evals.run_eval --mode full --report evals/report.md
```

`evals/samples/` ships with a synthetic starter set (clean, multi-page, bad
totals, ambiguous European date, implied currency, OCR-forcing "scanned"
invoice, plus two non-invoice documents for the future classify stage) - see
`evals/samples/README.md`. These are simple computer-rendered text, not real
invoices; replace/supplement with real hand-labeled ones before trusting the
accuracy numbers. No baseline has been recorded in `evals/report.md` yet since
this environment has no working `GEMINI_API_KEY`.

## Learnings

_Fill this in against real invoices - these are the interview stories._

- **Scanned/blurry invoice:**
- **European date format (e.g. 03/04):**
- **Currency only implied by the address:**
- **Line items that don't add up:**
- **Multi-page invoice:**
- **Handwritten note on the invoice:**
- **Same file uploaded twice:**

## Status against NEW_PLAN.md

Done: **Phase 0** (synthetic eval samples + harness - see caveat above about
real vs. synthetic data), **Phase 1** (folder structure, Alembic migrations,
full target schema, `Storage` interface), **Phase 2** (content-based file-type
sniffing, size/page limits, encrypted/corrupt-PDF rejection), **Phase 3**
(stage-based orchestrator, `SELECT FOR UPDATE SKIP LOCKED` + stale-lock
reclaim, verified live).

Not started: **Phase 4** (per-word bounding boxes, page images, splitting),
**Phase 5** (classify/route - `doc_type` is hardcoded to `"invoice"`, the
`classified` status exists in the enum but nothing sets it), **Phase 6**
(grounding - `field_values.page_no/bbox/source_text` are always null),
**Phase 7** (3-stage validation w/ cross-checks against a suppliers table -
today's `validate.py` is the schema+business-rules layers only, no
duplicate-invoice/supplier-lookup cross-check), **Phase 8** (reviewer-model
agree/disagree pass), **Phase 9** (per-field confidence combining
grounding+rules+reviewer+escalation - today's confidence is document-level
only), **Phase 10** (review UI, export webhook, `corrections` table is defined
but unused), **Phase 11** (comparative eval report against a real baseline,
mocked-LLM integration test, crash-and-resume as an automated test rather than
a manually-verified one).
