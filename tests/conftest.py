"""DB-backed test fixtures.

Most of this project's tests are deliberately DB-free pure-function tests
(see the other test files) - but NEW_PLAN.md's Phase 11 explicitly wants an
integration test of the whole pipeline, an idempotency test, and a
crash-and-resume test, and those genuinely need a real Postgres (JSONB columns
aren't SQLite-compatible, and `SELECT ... FOR UPDATE SKIP LOCKED` is a real
locking primitive, not something you can fake).

`db_session` skips cleanly if Postgres isn't reachable, so `pytest` stays
green without infra (e.g. this sandboxed dev environment) but runs for real
wherever a DB is available: `docker compose up -d db && alembic upgrade head`
first, then `pytest`.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from app.core.db import Base, get_engine, get_sessionmaker


@pytest.fixture
def db_session():
    engine = get_engine()
    try:
        with engine.connect():
            pass
    except OperationalError:
        pytest.skip("Postgres not reachable - run `docker compose up -d db && alembic upgrade head` for DB-backed tests")

    Base.metadata.create_all(engine)
    Session = get_sessionmaker()
    session = Session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
