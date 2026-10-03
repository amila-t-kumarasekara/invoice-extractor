# Invoice Extractor

A pipeline for turning uploaded documents into validated, structured JSON -
built to demonstrate model routing, validation layers, idempotency, grounding,
and evaluation. Built against the full 11-phase plan in `NEW_PLAN.md`; see
[Status against NEW_PLAN.md](#status-against-new_planmd) at the bottom for
what's real versus what's an honest known gap.

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
   worker: SELECT ... FOR UPDATE SKIP LOCKED   <---------------------------+
   (also reclaims jobs stuck `processing` past a stale-lock timeout)       |
                              |                                             |
                              v                                             |
                 orchestrator.run_stage(job.stage)                         |
                              |                                             |
      +----------+----------+----------+----------+                       |
      v          v          v          v                                  |
  ParseStage ClassifyStage ExtractStage ValidateStage                     |
  text layer/ one call     cheap model  re-derive cheap record + its       |
  OCR, word   classifies   -> grounded  grounding from stored `raw` (no    |
  boxes,      every page;  schema per   LLM call) -> business rules ->    |
  rendered    groups of    field, per-  cross-checks (dup invoice #,      |
  page image  differing    field        unknown supplier) -> AI reviewer  |
  -> safety.  type split   {value,      pass -> escalate to strong model  |
  scan_text   into child   page_no,     if any rule error, ungrounded     |
  (prompt-    documents;   source_text} required field, or reviewer      |
  injection/  unknown/low- -> ground_   disagreement (with the specific   |
  SQLi in     confidence   field()      reasons in the prompt) -> re-run  |
  text) ->    -> needs_    locates the  rules+cross-checks+reviewer on    |
  persist     review       real bbox    the escalated result -> per-field |
  `pages`     instead of   by searching confidence gate (grounded + rules |
      |       guessing     real page    passed + reviewer agreed + not    |
      |       a schema     words        escalated) -> document score =    |
      |            |            |       min over required fields          |
      v            v            v            |                            |
  status=     status=     status=            v                            |
  parsed      classified  extracted    status=approved | needs_review ----+
      |            |            |            |
      +--- enqueue next stage's job ---------+

   any stage raises SecurityRejection --> status=rejected, no retry
   any other exception --> attempts++, backoff, retry, or -> `failed` (dead-letter)
   `approved` (from the pipeline OR a human correction) --> webhook fired
```

Each stage commits its own DB writes atomically and reads its input back from
the DB (not from in-memory state) - that's what makes a stage safe to re-run:
if the worker dies mid-stage, nothing half-finished was committed, and a
restarted worker's stale-lock reclaim picks the job back up without redoing
already-completed stages. Verified live and as an automated integration test
(`tests/test_integration.py`) - see the Tests section.

## Stack

Python 3.12, FastAPI, Postgres + Alembic migrations (Docker Compose),
SQLAlchemy, Pydantic v2, PyMuPDF + Tesseract for OCR, `filetype` for
content-based MIME sniffing, Gemini (`gemini-2.5-flash` cheap/fast,
`gemini-2.5-pro` strong, via `google-genai`'s Interactions API), pytest.
A plain HTML upload page at `/` and a review page with bounding-box
highlighting at `/review/{id}`.

## Key design decisions

1. **Idempotency at upload** (`app/api/upload.py`). Unique key
   `(tenant_id, file_hash)` - re-uploading the same bytes for the same tenant
   returns the existing document. A `UNIQUE` constraint backs this up so a
   race between two identical concurrent uploads can't create two rows.

2. **Upload-time file safety** (`app/pipeline/safety.py::scan_upload`). Real
   content-type sniffing (magic bytes, not the filename extension), a size
   limit, a page-count limit, and rejection of encrypted/corrupt PDFs - all
   before the file is written to storage or a job exists.

3. **The job queue lives in Postgres** (`app/queue/`), not Redis/SQS. Workers
   claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`; a job stuck
   `processing` past `STALE_LOCK_MINUTES` is reclaimable (its owner presumably
   crashed) - verified live by forcing a job into that state and watching a
   running worker pick it back up. Ordinary failures retry with exponential
   backoff; exhausting `MAX_JOB_ATTEMPTS` moves the job/document to `failed`
   (dead-letter).

4. **The pipeline is staged, not one function** (`app/pipeline/orchestrator.py`).
   `parse -> classify -> extract -> validate` are four separate jobs against
   the same document, each committing its output before enqueuing the next.
   Verified live: forcing `extract` to fail/retry repeatedly never duplicated
   the already-committed `pages` rows from `parse`.

5. **Classify and route, don't assume** (`app/pipeline/classify.py`,
   `app/doctypes/registry.py`). One model call classifies *every page*; a
   type outside the registry or below `CLASSIFY_CONFIDENCE_THRESHOLD` becomes
   `doc_type="unknown"` + `needs_review` instead of being forced through a
   schema/prompt that doesn't fit. Adding a new document type is a new
   `app/doctypes/<name>/` package plus one registry entry, not new pipeline
   code - only `invoice` is implemented today.

6. **Page splitting** (`ClassifyStage`). If an upload's pages don't all
   classify the same way, consecutive same-type pages are grouped: the first
   group continues as the original document, later groups become child
   `Document` rows (`split_from_document_id`), each with its own copied
   `pages` rows and its own pipeline run from wherever its type/confidence
   lands it. Verified live (stubbed classification, real Postgres): a 4-page
   mixed upload correctly produced a 2-page primary + a 2-page child in
   `needs_review`, with no `extract` job for the unknown-type child.

7. **Parse before the LLM, and ground what comes back**
   (`app/pipeline/parse.py`, `app/pipeline/grounding.py`). Every page tries
   its embedded text layer first; only sparse pages fall back to Tesseract
   OCR (verified against a synthetic "scanned" invoice - real OCR noise:
   "Northwind Traders" → "Nerthiwined Traders"). Every page is also rendered
   to a PNG and its words captured as bounding boxes, in the *same* pixel
   space as the image (text-layer points scaled to match Tesseract's native
   pixel output) - the review UI draws highlights with zero extra scaling
   math. The model returns `{value, page_no, source_text}` per field;
   `grounding.py` never trusts the model's claim, it searches the page's real
   word boxes for `source_text` and only if found records a bbox - a
   deterministic, free check that catches "self-consistent hallucination"
   (a fabricated value the model is confident about) that arithmetic
   validation alone can't.

8. **Rule validation + cross-checks, three stages**
   (`app/doctypes/invoice/rules.py`). Schema (Pydantic itself, during
   `safe_build_invoice`), business rules (line items sum to subtotal,
   subtotal + tax = total, due date not before invoice date, valid ISO 4217
   currency, required fields present), and cross-checks against this
   tenant's other data (duplicate `(supplier, invoice_number)`, and an
   unrecognized supplier against a small seeded `suppliers` table standing in
   for master data - both warnings, since either might be legitimate).

9. **Escalation on rules, grounding, *or* reviewer disagreement**
   (`app/pipeline/orchestrator.py::ValidateStage`). Not just rule errors: a
   required field that couldn't be grounded, or that an independent AI
   review flagged as not matching its cited text, also triggers a strong-model
   re-run - with the *specific* reasons (which fields, why) in the prompt.

10. **An independent AI reviewer** (`app/pipeline/reviewer.py`). A second,
    narrow model call: "does this value match this cited text" - not
    re-extraction, not a correctness judgment, just a faithfulness check.
    Runs on the cheap model even during escalation (it's a cheaper, easier
    question than extraction itself).

11. **Per-field confidence gate** (`app/pipeline/review.py::compute_confidence`).
    Each field's score averages four signals: grounded, rules passed
    (errors only, not warnings), reviewer agreed, not escalated - each
    vacuously 1.0 when there's nothing to check (e.g. a correctly-null
    optional field). The document's score is the *minimum* across required
    fields - its least-confident required field caps the whole document.
    Below `CONFIDENCE_THRESHOLD`, status is `needs_review`, not `approved`.

12. **Review UI + corrections + webhook** (`app/api/review.py`). The needs-review
    page shows each page's rendered image with color-coded bounding-box
    overlays (confidence-scaled) and an editable form. Saving writes a
    `Correction` row per changed field (audit trail / future few-shot
    examples), updates the field, and approves the document - verified live
    end to end, including the `Correction` audit rows landing correctly in
    Postgres. Approval (pipeline or human) fires a best-effort webhook
    (`WEBHOOK_URL`) - failure is logged, never raised.

13. **Storage behind an interface** (`app/core/storage.py`). `Storage.save()`
    returns an opaque `storage_key`; only the concrete backend
    (`LocalDiskStorage`, default) knows it's a path. A `MinioStorage`
    implementation exists and is real, tested code against the `minio` SDK -
    **not** wired into `docker-compose.yml`, because MinIO Inc. archived
    their OSS server and pulled its images from Docker Hub, and
    `quay.io/minio/minio` (their suggested replacement) was returning 401s
    from what looked like a degraded registry when this was built. Bundling
    a service I couldn't verify actually starts would just hand you a broken
    `docker compose up`. Point `MINIO_ENDPOINT` at any S3-compatible server
    you have access to and set `STORAGE_BACKEND=minio` to use it.

14. **Eval harness** (`evals/run_eval.py`). Mirrors the real
    extract→ground→validate→review→escalate flow (minus the cross-tenant
    cross-checks, which need real *other* documents to mean anything) without
    needing a live Postgres/worker. `--mode cheap-only|cheap+validate|full`
    reruns the same samples through progressively more of the pipeline - the
    "accuracy went from X% to Y%" story.

15. **Input safety is a keyword/structure denylist, not model judgment**
    (`app/pipeline/safety.py`). Beyond upload-time file checks: parsed text is
    scanned for prompt-injection phrasing *and* classic SQL-injection payload
    shapes before it ever reaches an LLM - a hit means `rejected`, no retry,
    no LLM call. Every DB query already uses the SQLAlchemy ORM with bound
    parameters, so the SQLi check is defense-in-depth, not a live-vulnerability
    fix. Deliberately not an LLM classifier: a fixed list can't be talked out
    of its job by adversarial phrasing, is free, and is fully auditable.

## Database tables

- `documents`: id, tenant_id, file_hash (unique w/ tenant_id), filename, mime_type, storage_key, doc_type (null until classified), status, confidence, split_from_document_id, page_start/page_end (non-null only for a split group), created_at, updated_at
- `pages`: id, document_id, page_no, text_source (text_layer/ocr), text, image_key, words ([{text, bbox}, ...])
- `jobs`: id, document_id, stage (parse/classify/extract/validate), status, attempts, locked_at, next_run_at, last_error
- `extractions`: id, document_id, model, attempt_no, raw (JSONB, the grounded `{value,page_no,source_text}` shape), tokens, cost_usd, latency_ms - one row per model call (cheap, and strong if escalated)
- `field_values`: id, extraction_id, field, value, page_no, bbox, source_text, confidence (the final per-field score)
- `validation_issues`: id, extraction_id, field, rule, severity, message
- `suppliers`: id, tenant_id, name - seeded via `scripts/seed_suppliers.py`
- `corrections`: id, field_value_id, old_value, new_value, reviewer, created_at

`documents.status`: `uploaded -> parsed -> classified -> extracted -> validated -> approved | needs_review | failed | rejected`

## Folder structure

```
invoice-extractor/
  app/
    api/
      upload.py       # POST /documents - safety checks, storage, idempotency
      documents.py    # GET /documents/{id}, page image serving
      review.py       # needs-review list, review UI, corrections
      ui.py            # HTML upload page at /
    core/
      config.py, db.py, storage.py, webhooks.py, logging.py
    queue/
      jobs.py           # claim_job (SKIP LOCKED + stale-lock reclaim), backoff
      worker.py          # poll loop
    pipeline/
      safety.py          # file-type/size/encryption + keyword denylist checks
      parse.py            # text layer + OCR + word boxes + page images
      classify.py          # per-page classification + grouping for splitting
      extract.py            # LLM + Pydantic (forced function call), grounded
      grounding.py           # deterministic source_text -> bbox search
      reviewer.py             # independent AI faithfulness check
      review.py                # per-field confidence gate
      orchestrator.py           # Stage protocol + all four stages
    doctypes/
      registry.py                # name -> schema/prompt/tool/rules
      invoice/schema.py, prompt.md, rules.py, tool_schema.py
    models.py                     # shared Issue schema
  evals/
    samples/                       # PDFs + expected JSON (synthetic starter set)
    run_eval.py, report.md
  migrations/                       # Alembic
  scripts/seed_suppliers.py
  tests/
  docker-compose.yml, Dockerfile, alembic.ini
  NEW_PLAN.md                        # the 11-phase plan this was built against
```

## Running it

```bash
cp .env.example .env
# edit .env: set GEMINI_API_KEY (https://ai.google.dev/gemini-api/docs/api-key)

docker compose up --build
docker compose run --rm api python -m scripts.seed_suppliers demo
```

`docker compose up` brings up `db`, then a one-shot `migrate` service
(`alembic upgrade head`), then `api` and `worker`. (Earlier in this build,
`api` and `worker` both independently called `Base.metadata.create_all()` on
startup and raced each other into a duplicate-key crash - the `migrate`
service exists specifically so schema creation is one ordered step, not two
services racing.)

- Upload UI: http://localhost:8000/
- Review UI: http://localhost:8000/review (list), `/review/{id}` (detail)
- API:
  ```bash
  curl -F tenant_id=demo -F file=@invoice.pdf http://localhost:8000/documents
  curl http://localhost:8000/documents/<document_id>
  curl http://localhost:8000/review?tenant_id=demo
  ```

To run outside Docker:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# macOS: brew install tesseract
docker compose up -d db
alembic upgrade head
python -m scripts.seed_suppliers demo
python -m app.queue.worker      # in one terminal
uvicorn app.api:app --reload    # in another
```

## Tests

```bash
pytest
```

68 tests total. Most are DB-free pure-function tests: rule validation,
`safe_build_invoice`'s field-by-field degradation, confidence scoring,
text-layer/OCR parsing, grounding's bbox search, page-classification
grouping, and the full safety denylist (PDF structure, prompt-injection,
SQL-injection, with a false-positive guard so "Union Bank Select Corp"
doesn't trigger the SQLi scan).

Three tests in `tests/test_integration.py` need a real Postgres (JSONB
columns and `SELECT ... FOR UPDATE SKIP LOCKED` aren't SQLite-compatible) -
they skip cleanly without one, or run for real after
`docker compose up -d db && alembic upgrade head`:

- **Full pipeline, LLM mocked**: parsing, word-box extraction, grounding,
  business rules, cross-checks, and confidence scoring are all real; only
  `classify_pages`/`extract_document`/`review_fields` are mocked. Building
  this caught a real subtlety in the resumability design: `ValidateStage`
  re-derives the record from the *persisted* `extraction.raw`, not from any
  in-memory state left over from `ExtractStage` - the first draft of this
  test passed a stub with an empty `raw` and the test correctly failed by
  trying to escalate with a real (unmocked) client, because the pipeline was
  faithfully reading back what was actually in the database.
- **Idempotency**: same `(tenant_id, file_hash)` resolves to one row.
- **Crash and resume**: a job forced into a stale `processing` state is
  reclaimed by `claim_job`.

Beyond the automated suite, these were also verified live against a real
`docker compose up` stack: a `.exe` renamed `.pdf` rejected by content
sniffing; a `/JavaScript`+`/OpenAction` PDF rejected at upload with nothing
written to disk; prompt-injection and `DROP TABLE` payloads in invoice text
accepted at upload (clean structure) but rejected by the worker right after
parsing with zero LLM calls; the classify/split grouping logic (stubbed
classification, real Postgres) correctly creating a child document with the
right page range and no job for an unknown-type child; the known-supplier and
duplicate-invoice-number cross-checks against real seeded data; and the full
review-UI correction flow (`Correction` audit rows landing correctly).

## Eval harness

```bash
python -m evals.samples.generate_synthetic_samples   # already run; regenerate anytime
python -m evals.run_eval --mode cheap-only
python -m evals.run_eval --mode cheap+validate
python -m evals.run_eval --mode full --report evals/report.md
```

`evals/samples/` ships with a synthetic starter set (clean, multi-page, bad
totals, ambiguous European date, implied currency, OCR-forcing "scanned"
invoice, plus two non-invoice documents for classify testing) - see
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
- **A batch upload with more than one document type in it:**

**Honest gaps, not silently glossed over:**

- **MinIO isn't bundled in `docker-compose.yml`.** The code is real (`app/core/storage.py::MinioStorage`,
  built against the actual `minio` SDK), but MinIO Inc. archived their OSS
  server and removed it from Docker Hub, and their suggested `quay.io`
  replacement was returning 401s from a registry that looked degraded when
  this was built. See design decision #13.
- **Cross-tenant cross-checks aren't in the eval harness.** They need real
  *other* documents in a real database to mean anything; an isolated eval run
  doesn't have that context. Verified live instead (see Tests).
- **The reviewer's own cost/latency isn't persisted per-document**, only
  logged - it's not stored as its own `extractions` row (that table's "one
  row per model call" semantics are about *extraction* attempts specifically).
  A document's true total LLM cost is slightly higher than
  `extractions.cost_usd` summed, by the reviewer pass(es).
- **No real accuracy baseline yet** - needs a working `GEMINI_API_KEY` and
  ideally real invoices, not the synthetic set.
