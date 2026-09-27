"""Job worker loop.

The queue lives in Postgres itself: workers grab the next `queued` job with
`SELECT ... FOR UPDATE SKIP LOCKED`, which lets any number of worker processes
poll the same table concurrently without double-processing a job or needing a
separate broker (Redis/SQS). At real scale you'd swap this table for SQS or
Pub/Sub, but for a single-database service this is one less moving part.

Retry policy: on failure, attempts += 1 and next_attempt_at is pushed out with
exponential backoff. Once attempts >= MAX_JOB_ATTEMPTS the job (and its
document) move to `failed` permanently - a dead-letter state a human has to
look at, rather than a job silently retrying forever.

Security: parsed text is scanned for prompt-injection keywords
(app/pipeline/security.py) before it's ever sent to an LLM. A hit is a
`SecurityRejection`, handled separately from ordinary failures - it goes
straight to `rejected` with no retry, since retrying doesn't change what's in
the document.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from google import genai
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import (
    Document,
    DocumentStatus,
    Extraction,
    Job,
    JobStatus,
    get_sessionmaker,
    init_db,
    utcnow,
)
from app.pipeline.parse import parse_pdf
from app.pipeline.review import run_pipeline
from app.pipeline.security import SecurityRejection, scan_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("worker")

POLL_INTERVAL_SECONDS = 2.0


def claim_job(db: Session) -> Job | None:
    job = (
        db.query(Job)
        .filter(Job.status == JobStatus.queued.value, Job.next_attempt_at <= utcnow())
        .order_by(Job.created_at)
        .with_for_update(skip_locked=True)
        .first()
    )
    if job is None:
        return None
    job.status = JobStatus.processing.value
    job.locked_at = utcnow()
    db.commit()
    return job


def backoff_seconds(attempts: int, base: int) -> int:
    return base * (2 ** max(0, attempts - 1))


def handle_job(db: Session, job: Job, settings: Settings, client: genai.Client) -> None:
    document = db.get(Document, job.document_id)
    if document is None:
        job.status = JobStatus.failed.value
        job.last_error = "document not found"
        db.commit()
        return

    document.status = DocumentStatus.processing.value
    db.commit()

    parsed = parse_pdf(document.storage_path, min_chars_per_page=settings.ocr_text_layer_min_chars)
    document_text = parsed.as_prompt_text()

    injection_hits = scan_text(document_text)
    if injection_hits:
        raise SecurityRejection(injection_hits)

    outcome = run_pipeline(client, settings, document_text)

    for attempt in outcome.attempts:
        db.add(
            Extraction(
                document_id=document.id,
                model=attempt.call.model,
                data=attempt.call.invoice.model_dump(mode="json"),
                issues=[issue.model_dump() for issue in attempt.issues],
                confidence=attempt.confidence,
                cost_usd=attempt.call.cost_usd,
                latency_ms=attempt.call.latency_ms,
            )
        )

    document.status = outcome.status
    job.status = JobStatus.done.value
    db.commit()

    logger.info(
        "document=%s model=%s escalated=%s status=%s confidence=%.2f cost=$%.4f",
        document.id,
        outcome.model,
        outcome.escalated,
        outcome.status,
        outcome.confidence,
        outcome.cost_usd,
    )


def process_next_job() -> bool:
    """Claim and process one job. Returns True if a job was found (whether or
    not it succeeded), False if the queue was empty."""
    settings = get_settings()
    Session = get_sessionmaker()
    client = genai.Client(api_key=settings.gemini_api_key)

    db = Session()
    try:
        job = claim_job(db)
        if job is None:
            return False

        try:
            handle_job(db, job, settings, client)
        except SecurityRejection as exc:
            # Not a transient failure - retrying won't change what's in the
            # document, so skip the backoff/retry path entirely.
            db.rollback()
            job = db.get(Job, job.id)
            document = db.get(Document, job.document_id)
            job.status = JobStatus.failed.value
            job.last_error = f"blocked by security scan: {'; '.join(exc.reasons)}"[:2000]
            if document is not None:
                document.status = DocumentStatus.rejected.value
            logger.warning("job=%s document=%s rejected: %s", job.id, job.document_id, exc.reasons)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - job failures must not crash the worker loop
            db.rollback()
            job = db.get(Job, job.id)
            document = db.get(Document, job.document_id)
            job.attempts += 1
            job.last_error = str(exc)[:2000]
            logger.exception("job=%s failed (attempt %s)", job.id, job.attempts)

            if job.attempts >= settings.max_job_attempts:
                job.status = JobStatus.failed.value
                if document is not None:
                    document.status = DocumentStatus.failed.value
            else:
                job.status = JobStatus.queued.value
                delay = backoff_seconds(job.attempts, settings.job_backoff_base_seconds)
                job.next_attempt_at = utcnow() + timedelta(seconds=delay)
            db.commit()
        return True
    finally:
        db.close()


def run_forever() -> None:
    init_db()
    logger.info("worker started, polling every %ss", POLL_INTERVAL_SECONDS)
    while True:
        found_work = process_next_job()
        if not found_work:
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_forever()
