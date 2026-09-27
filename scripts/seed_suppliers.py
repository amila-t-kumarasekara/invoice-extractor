"""Seeds a small per-tenant known-suppliers allowlist - stands in for real
master data (NEW_PLAN.md Phase 7's supplier cross-check). Idempotent: safe to
run multiple times, existing (tenant_id, name) pairs are left alone.

Usage:
    python -m scripts.seed_suppliers [tenant_id]

Defaults to tenant_id="demo" and the supplier names used by
evals/samples/generate_synthetic_samples.py, so the synthetic eval set has at
least some documents that pass the known-supplier check.
"""
from __future__ import annotations

import sys

from app.core.db import Supplier, get_sessionmaker

DEMO_SUPPLIERS = (
    "Acme Co",
    "Globex Manufacturing",
    "Barrow & Sons Ltd",
)


def seed(tenant_id: str, names: tuple[str, ...] = DEMO_SUPPLIERS) -> None:
    Session = get_sessionmaker()
    db = Session()
    try:
        existing = {s.name for s in db.query(Supplier).filter_by(tenant_id=tenant_id).all()}
        added = 0
        for name in names:
            if name not in existing:
                db.add(Supplier(tenant_id=tenant_id, name=name))
                added += 1
        db.commit()
        print(f"tenant={tenant_id}: added {added} new supplier(s), {len(names) - added} already present")
    finally:
        db.close()


if __name__ == "__main__":
    tenant = sys.argv[1] if len(sys.argv) > 1 else "demo"
    seed(tenant)
