"""Job worker loop.

Claims one job (one stage, for one document) at a time, runs it through
`app.pipeline.orchestrator.run_stage`, and either enqueues the next stage's
job or - on failure - retries with exponential backoff up to
`MAX_JOB_ATTEMPTS`, after which the document moves to `failed` permanently
(dead-letter). A `SecurityRejection` is handled separately from ordinary
failures: it's not transient, so it skips the backoff/retry path entirely and
goes straight to `rejected`.

See app/pipeline/orchestrator.py for why stage-level (not LLM-call-level)
granularity is the resumability boundary here.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from google import genai

from app.core.config import get_settings
from app.core.db import Document, DocumentStatus, Job, JobStatus, get_sessionmaker, utcnow
from app.core.logging import configure_logging
from app.pipeline.orchestrator import run_stage
from app.pipeline.safety import SecurityRejection
from app.queue.jobs import backoff_seconds, claim_job

configure_logging()
logger = logging.getLogger("worker")

POLL_INTERVAL_SECONDS = 2.0


def handle_job(db, job: Job, settings, client: genai.Client) -> None:
    document = db.get(Document, job.document_id)
    if document is None:
        job.status = JobStatus.failed.value
        job.last_error = "document not found"
        db.commit()
        return

    next_stage = run_stage(db, document, job.stage, settings, client)
    job.status = JobStatus.done.value
    db.commit()

    if next_stage is not None:
        db.add(Job(document_id=document.id, stage=next_stage, status=JobStatus.queued.value))
        db.commit()

    logger.info("document=%s stage=%s -> status=%s next_stage=%s", document.id, job.stage, document.status, next_stage)


def process_next_job() -> bool:
    """Claim and process one job. Returns True if a job was found (whether or
    not it succeeded), False if the queue was empty."""
    settings = get_settings()
    Session = get_sessionmaker()
    client = genai.Client(api_key=settings.gemini_api_key)

    db = Session()
    try:
        job = claim_job(db, settings.stale_lock_minutes)
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
            logger.exception("job=%s stage=%s failed (attempt %s)", job.id, job.stage, job.attempts)

            if job.attempts >= settings.max_job_attempts:
                job.status = JobStatus.failed.value
                if document is not None:
                    document.status = DocumentStatus.failed.value
            else:
                job.status = JobStatus.queued.value
                delay = backoff_seconds(job.attempts, settings.job_backoff_base_seconds)
                job.next_run_at = utcnow() + timedelta(seconds=delay)
            db.commit()
        return True
    finally:
        db.close()


def run_forever() -> None:
    # Schema is managed by `alembic upgrade head` (see the `migrate` service in
    # docker-compose.yml), not by the worker - two long-running processes both
    # racing to CREATE TABLE on startup is exactly the bug that cost us a
    # worker crash during testing (both api and worker called Base.metadata
    # create_all() concurrently and one lost a duplicate-key race).
    logger.info("worker started, polling every %ss", POLL_INTERVAL_SECONDS)
    while True:
        found_work = process_next_job()
        if not found_work:
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_forever()
