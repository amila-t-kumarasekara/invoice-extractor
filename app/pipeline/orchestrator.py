"""Stage protocol + the state machine that drives a document through it.

`uploaded -> parsed -> extracted -> validated -> approved | needs_review | failed | rejected`

Each stage is a separate job row (`app.core.db.Job.stage`), and each stage's
`run()` does all of its DB writes in one transaction, committed only by the
caller (`app/queue/worker.py`) after `run()` returns successfully. That's what
makes a stage "safe to re-run": if the process is killed mid-stage, nothing
from that half-finished attempt was ever committed - Postgres rolls the open
transaction back automatically, and a retry starts that one stage cleanly from
scratch. Stages that already completed and committed (e.g. `parse`, or the
cheap-model call in `extract`) are never redone, because their output already
lives in `pages`/`extractions` and the next stage reads it from there instead
of from in-memory state.

Honest tradeoff, worth saying out loud: this gives resumability at *stage*
granularity, not exactly-once at the LLM-call granularity. If the process
dies between a successful LLM response and the commit at the end of that
stage, the retry redoes that one LLM call. That's the same at-least-once
tradeoff every job queue makes; avoiding it entirely would mean committing
after every LLM call, which is what the old single-function pipeline
effectively couldn't do (see git history / prior conversation) - splitting
parse/extract/validate into separate stages is what narrows the redo window
down to "one stage's LLM call" instead of "the whole pipeline."
"""
from __future__ import annotations

from datetime import date
from typing import Protocol

from google import genai
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import (
    Document,
    DocumentStatus,
    Extraction,
    FieldValue,
    JobStage,
    Page,
    ValidationIssue,
)
from app.core.storage import get_storage
from app.models import ALL_FIELDS, Issue
from app.pipeline.extract import ExtractionCall, extract_invoice, safe_build_invoice
from app.pipeline.parse import parse_pdf
from app.pipeline.review import compute_confidence
from app.pipeline.safety import SecurityRejection, scan_text
from app.pipeline.validate import validate_invoice


class Stage(Protocol):
    name: str

    def run(self, db: Session, document: Document, settings: Settings, client: genai.Client) -> str | None:
        """Do this stage's work, persist it, update `document.status`. Return
        the next stage's name to enqueue, or None if this stage reached a
        terminal document status (approved/needs_review/rejected)."""
        ...


def _json_value(value):
    if isinstance(value, date):
        return value.isoformat()
    return value


def _load_document_text(db: Session, document: Document) -> str:
    pages = db.query(Page).filter_by(document_id=document.id).order_by(Page.page_no).all()
    return "\n\n".join(f"--- Page {p.page_no} ({p.text_source}) ---\n{p.text}" for p in pages)


def _persist_extraction(db: Session, document: Document, attempt_no: int, call: ExtractionCall) -> Extraction:
    extraction = Extraction(
        document_id=document.id,
        model=call.model,
        attempt_no=attempt_no,
        raw=call.raw,
        tokens={"input": call.input_tokens, "output": call.output_tokens},
        cost_usd=call.cost_usd,
        latency_ms=call.latency_ms,
    )
    db.add(extraction)
    db.flush()  # need extraction.id for the FK on child rows below

    for field_name in ALL_FIELDS:
        db.add(FieldValue(extraction_id=extraction.id, field=field_name, value=_json_value(getattr(call.invoice, field_name))))

    return extraction


def _persist_issues(db: Session, extraction: Extraction, issues: list[Issue]) -> None:
    for issue in issues:
        db.add(
            ValidationIssue(
                extraction_id=extraction.id,
                field=issue.field,
                rule=issue.code,
                severity=issue.severity,
                message=issue.message,
            )
        )


class ParseStage:
    name = JobStage.parse.value

    def run(self, db: Session, document: Document, settings: Settings, client: genai.Client) -> str | None:
        local_path = get_storage().path_for_local_tools(document.storage_key)
        parsed = parse_pdf(local_path, min_chars_per_page=settings.ocr_text_layer_min_chars)

        for page in parsed.pages:
            db.add(Page(document_id=document.id, page_no=page.page_number, text_source=page.source, text=page.text))

        injection_hits = scan_text(parsed.as_prompt_text())
        if injection_hits:
            raise SecurityRejection(injection_hits)

        document.status = DocumentStatus.parsed.value
        return JobStage.extract.value


class ExtractStage:
    name = JobStage.extract.value

    def run(self, db: Session, document: Document, settings: Settings, client: genai.Client) -> str | None:
        text = _load_document_text(db, document)
        call = extract_invoice(
            client,
            model=settings.cheap_model,
            document_text=text,
            price_in_per_million=settings.cheap_model_price_in,
            price_out_per_million=settings.cheap_model_price_out,
        )
        extraction = _persist_extraction(db, document, attempt_no=1, call=call)
        _persist_issues(db, extraction, call.pre_issues)

        document.status = DocumentStatus.extracted.value
        return JobStage.validate.value


class ValidateStage:
    name = JobStage.validate.value

    def run(self, db: Session, document: Document, settings: Settings, client: genai.Client) -> str | None:
        cheap_extraction = (
            db.query(Extraction).filter_by(document_id=document.id, attempt_no=1).one()
        )
        cheap_invoice, cheap_pre_issues = safe_build_invoice(cheap_extraction.raw)
        cheap_rule_issues = validate_invoice(cheap_invoice)
        _persist_issues(db, cheap_extraction, cheap_rule_issues)

        all_issues = cheap_pre_issues + cheap_rule_issues
        errors = [i for i in all_issues if i.severity == "error"]

        final_invoice = cheap_invoice
        final_issues = all_issues
        escalated = False

        if errors:
            escalated = True
            text = _load_document_text(db, document)
            strong_call = extract_invoice(
                client,
                model=settings.strong_model,
                document_text=text,
                price_in_per_million=settings.strong_model_price_in,
                price_out_per_million=settings.strong_model_price_out,
                prior_issues=[e.message for e in errors],
            )
            strong_extraction = _persist_extraction(db, document, attempt_no=2, call=strong_call)
            strong_rule_issues = validate_invoice(strong_call.invoice)
            _persist_issues(db, strong_extraction, strong_call.pre_issues + strong_rule_issues)

            final_invoice = strong_call.invoice
            final_issues = strong_call.pre_issues + strong_rule_issues

        confidence = compute_confidence(final_invoice, final_issues, escalated)
        document.confidence = confidence
        document.status = (
            DocumentStatus.approved.value if confidence >= settings.confidence_threshold else DocumentStatus.needs_review.value
        )
        return None


STAGES: dict[str, Stage] = {
    JobStage.parse.value: ParseStage(),
    JobStage.extract.value: ExtractStage(),
    JobStage.validate.value: ValidateStage(),
}


def run_stage(db: Session, document: Document, stage_name: str, settings: Settings, client: genai.Client) -> str | None:
    return STAGES[stage_name].run(db, document, settings, client)
