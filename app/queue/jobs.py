"""Job claiming: the queue lives in Postgres, not Redis/SQS.

Workers claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`, so any number of
worker processes can poll the same table without double-claiming a row. A job
is claimable if it's queued and due (`next_run_at <= now()`), *or* if it's
stuck `processing` with a lock older than `stale_lock_minutes` - that second
clause is what lets a killed-and-restarted worker pick back up a job whose
previous owner died mid-stage, instead of leaving it locked forever.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.db import Job, JobStatus, utcnow


def claim_job(db: Session, stale_lock_minutes: int) -> Job | None:
    now = utcnow()
    stale_cutoff = now - timedelta(minutes=stale_lock_minutes)

    job = (
        db.query(Job)
        .filter(
            or_(
                and_(Job.status == JobStatus.queued.value, Job.next_run_at <= now),
                and_(Job.status == JobStatus.processing.value, Job.locked_at < stale_cutoff),
            )
        )
        .order_by(Job.created_at)
        .with_for_update(skip_locked=True)
        .first()
    )
    if job is None:
        return None

    job.status = JobStatus.processing.value
    job.locked_at = now
    db.commit()
    return job


def backoff_seconds(attempts: int, base_seconds: int) -> int:
    return base_seconds * (2 ** max(0, attempts - 1))
