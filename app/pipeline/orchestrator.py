"""Stage protocol + the state machine that drives a document through it.

`uploaded -> parsed -> classified -> extracted -> validated -> approved | needs_review | failed | rejected`
(a `split` upload also produces child documents - see ClassifyStage.)

Each stage is a separate job row (`app.core.db.Job.stage`), and each stage's
`run()` does all of its DB writes in one transaction, committed only by the
caller (`app/queue/worker.py`) after `run()` returns successfully. That's what
makes a stage "safe to re-run": if the process is killed mid-stage, nothing
from that half-finished attempt was ever committed - Postgres rolls the open
transaction back automatically, and a retry starts that one stage cleanly from
scratch. Stages that already completed and committed (parse, an already-made
LLM call) are never redone, because the next stage reads their output back
from the DB instead of from in-memory state.

One exception to "nothing half-finished survives a crash": `ParseStage` writes
page images to `Storage` *before* the DB transaction commits, since storage
writes aren't part of the Postgres transaction. It does this only after the
prompt-injection scan passes, specifically so a rejected document never has
images written for it at all - but a crash between a successful image write
and the stage's final commit would leak that one image on retry. Acceptable
for a local-disk/MinIO demo; a hardened version would write to a staging key
and rename-on-commit.

Honest tradeoff, worth saying out loud: this gives resumability at *stage*
granularity, not exactly-once at the LLM-call granularity. If the process
dies between a successful LLM response and the commit at the end of that
stage, the retry redoes that one LLM call.
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
    Job,
    JobStage,
    JobStatus,
    Page,
    ValidationIssue,
)
from app.core.storage import get_storage
from app.core.webhooks import fire_approved_webhook
from app.doctypes.registry import REGISTRY, DocTypeConfig
from app.models import Issue
from app.pipeline.classify import classify_pages, group_consecutive_pages
from app.pipeline.extract import ExtractionCall, extract_document, safe_build_invoice, unwrap_grounded_raw
from app.pipeline.grounding import ground_field
from app.pipeline.parse import parse_pdf
from app.pipeline.review import compute_confidence
from app.pipeline.reviewer import review_fields
from app.pipeline.safety import SecurityRejection, scan_text


class Stage(Protocol):
    name: str

    def run(self, db: Session, document: Document, settings: Settings, client: genai.Client) -> str | None:
        """Do this stage's work, persist it, update `document.status`. Return
        the next stage's name to enqueue, or None if this stage reached a
        terminal document status (approved/needs_review/rejected/split)."""
        ...


def _json_value(value):
    if isinstance(value, date):
        return value.isoformat()
    return value


def _load_document_pages(db: Session, document: Document) -> list[Page]:
    query = db.query(Page).filter_by(document_id=document.id)
    if document.page_start is not None:
        query = query.filter(Page.page_no >= document.page_start, Page.page_no <= document.page_end)
    return query.order_by(Page.page_no).all()


def _load_document_text(db: Session, document: Document) -> str:
    pages = _load_document_pages(db, document)
    return "\n\n".join(f"--- Page {p.page_no} ({p.text_source}) ---\n{p.text}" for p in pages)


def _load_page_words(db: Session, document: Document) -> dict[int, list[dict]]:
    return {p.page_no: (p.words or []) for p in _load_document_pages(db, document)}


def _load_grounding(db: Session, extraction_id: str) -> dict[str, dict | None]:
    rows = db.query(FieldValue).filter_by(extraction_id=extraction_id).all()
    result: dict[str, dict | None] = {}
    for row in rows:
        if row.page_no is not None and row.bbox is not None:
            result[row.field] = {"page_no": row.page_no, "bbox": row.bbox, "source_text": row.source_text}
        else:
            result[row.field] = None
    return result


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


def _persist_extraction(
    db: Session, document: Document, doctype: DocTypeConfig, attempt_no: int, call: ExtractionCall
) -> Extraction:
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
    db.flush()  # need extraction.id for FK on child rows below

    page_words = _load_page_words(db, document)
    for field_name in doctype.all_fields:
        value = getattr(call.record, field_name, None)
        hint = call.grounding_hints.get(field_name, {})
        grounded = ground_field(page_words, hint.get("page_no"), hint.get("source_text"))
        db.add(
            FieldValue(
                extraction_id=extraction.id,
                field=field_name,
                value=_json_value(value),
                page_no=grounded["page_no"] if grounded else None,
                bbox=grounded["bbox"] if grounded else None,
                source_text=grounded["source_text"] if grounded else None,
            )
        )

    if hasattr(call.record, "line_items"):
        db.add(
            FieldValue(
                extraction_id=extraction.id,
                field="line_items",
                value=[li.model_dump(mode="json") for li in call.record.line_items],
            )
        )

    return extraction


class ParseStage:
    name = JobStage.parse.value

    def run(self, db: Session, document: Document, settings: Settings, client: genai.Client) -> str | None:
        local_path = get_storage().path_for_local_tools(document.storage_key)
        parsed = parse_pdf(local_path, min_chars_per_page=settings.ocr_text_layer_min_chars)

        # Check safety BEFORE writing anything to storage - storage writes
        # aren't part of the DB transaction, so a rejected document should
        # never get page images written for it in the first place.
        injection_hits = scan_text(parsed.as_prompt_text())
        if injection_hits:
            raise SecurityRejection(injection_hits)

        storage = get_storage()
        for page in parsed.pages:
            image_key = storage.save(document.tenant_id, f"{document.file_hash}_page{page.page_number}.png", page.image_bytes)
            db.add(
                Page(
                    document_id=document.id,
                    page_no=page.page_number,
                    text_source=page.source,
                    text=page.text,
                    image_key=image_key,
                    words=[w.to_json() for w in page.words],
                )
            )

        document.status = DocumentStatus.parsed.value
        return JobStage.classify.value


class ClassifyStage:
    """Phase 5 (classify/route) + Phase 4's page-splitting: one call classifies
    every page; consecutive pages sharing a type become one document. A
    single-group result (the common case) just sets `doc_type` on the
    existing document. Multiple groups mean the upload actually contained more
    than one document - the first group continues as this document, and each
    additional group becomes a new child `Document` (`split_from_document_id`
    pointing back here), each entering the pipeline from wherever its own
    type/confidence lands it.
    """

    name = JobStage.classify.value

    def run(self, db: Session, document: Document, settings: Settings, client: genai.Client) -> str | None:
        pages = _load_document_pages(db, document)
        result = classify_pages(
            client,
            model=settings.cheap_model,
            pages=[(p.page_no, p.text) for p in pages],
            price_in_per_million=settings.cheap_model_price_in,
            price_out_per_million=settings.cheap_model_price_out,
            confidence_threshold=settings.classify_confidence_threshold,
        )
        groups = group_consecutive_pages(result.pages)
        pages_by_no = {p.page_no: p for p in pages}

        primary_type, primary_page_nos = groups[0]
        document.doc_type = primary_type
        if len(groups) > 1:
            document.page_start = min(primary_page_nos)
            document.page_end = max(primary_page_nos)

        for i, (doc_type, page_nos) in enumerate(groups[1:], start=1):
            child_status = DocumentStatus.classified.value if doc_type in REGISTRY else DocumentStatus.needs_review.value
            child = Document(
                tenant_id=document.tenant_id,
                file_hash=f"{document.file_hash}:split{i}",
                filename=f"{document.filename} [pages {min(page_nos)}-{max(page_nos)}]",
                mime_type=document.mime_type,
                storage_key=document.storage_key,
                doc_type=doc_type,
                status=child_status,
                split_from_document_id=document.id,
                page_start=min(page_nos),
                page_end=max(page_nos),
            )
            db.add(child)
            db.flush()
            for page_no in page_nos:
                src = pages_by_no[page_no]
                db.add(
                    Page(
                        document_id=child.id,
                        page_no=src.page_no,
                        text_source=src.text_source,
                        text=src.text,
                        image_key=src.image_key,
                        words=src.words,
                    )
                )
            if doc_type in REGISTRY:
                db.add(Job(document_id=child.id, stage=JobStage.extract.value, status=JobStatus.queued.value))

        if primary_type not in REGISTRY:
            document.status = DocumentStatus.needs_review.value
            return None

        document.status = DocumentStatus.classified.value
        return JobStage.extract.value


class ExtractStage:
    name = JobStage.extract.value

    def run(self, db: Session, document: Document, settings: Settings, client: genai.Client) -> str | None:
        doctype = REGISTRY[document.doc_type]
        text = _load_document_text(db, document)
        call = extract_document(
            client,
            doctype=doctype,
            model=settings.cheap_model,
            document_text=text,
            price_in_per_million=settings.cheap_model_price_in,
            price_out_per_million=settings.cheap_model_price_out,
        )
        extraction = _persist_extraction(db, document, doctype, attempt_no=1, call=call)
        _persist_issues(db, extraction, call.pre_issues)

        document.status = DocumentStatus.extracted.value
        return JobStage.validate.value


class ValidateStage:
    """Phase 7 (schema + business rules + cross-checks), Phase 8 (AI reviewer
    + escalation), Phase 9 (per-field confidence gate) - all in one stage,
    since none of the sub-steps here involve their own separately-resumable
    LLM call boundary the way parse/extract do (the escalation's strong-model
    call is the only LLM call in this stage besides the reviewer passes)."""

    name = JobStage.validate.value

    def run(self, db: Session, document: Document, settings: Settings, client: genai.Client) -> str | None:
        doctype = REGISTRY[document.doc_type]

        cheap_extraction = db.query(Extraction).filter_by(document_id=document.id, attempt_no=1).one()
        plain_raw, cheap_grounding_hints = unwrap_grounded_raw(cheap_extraction.raw, doctype.grounded_fields)
        cheap_record, cheap_pre_issues = safe_build_invoice(doctype.schema, plain_raw)

        cheap_rule_issues = doctype.validate_fn(cheap_record)
        cheap_cross_issues = doctype.cross_check_fn(db, document.tenant_id, document.id, cheap_record)
        _persist_issues(db, cheap_extraction, cheap_rule_issues + cheap_cross_issues)
        cheap_all_issues = cheap_pre_issues + cheap_rule_issues + cheap_cross_issues

        cheap_grounding = _load_grounding(db, cheap_extraction.id)
        cheap_values = {f: getattr(cheap_record, f, None) for f in doctype.all_fields}
        cheap_source_texts = {f: cheap_grounding_hints.get(f, {}).get("source_text") for f in doctype.all_fields}
        cheap_review = review_fields(
            client,
            model=settings.cheap_model,
            fields=doctype.all_fields,
            values=cheap_values,
            source_texts=cheap_source_texts,
            price_in_per_million=settings.cheap_model_price_in,
            price_out_per_million=settings.cheap_model_price_out,
        )

        errors = [i for i in cheap_all_issues if i.severity == "error"]
        ungrounded_required = [
            f for f in doctype.required_fields if cheap_grounding.get(f) is None and cheap_values.get(f) is not None
        ]
        disagreed_required = [f for f in doctype.required_fields if cheap_review.agreements.get(f) is False]

        final_record = cheap_record
        final_issues = cheap_all_issues
        final_extraction = cheap_extraction
        final_grounding = cheap_grounding
        final_review = cheap_review
        escalated = False

        if errors or ungrounded_required or disagreed_required:
            escalated = True
            reasons = [e.message for e in errors]
            if ungrounded_required:
                reasons.append(
                    f"Could not verify these fields actually appear in the document text as claimed: "
                    f"{', '.join(ungrounded_required)}"
                )
            if disagreed_required:
                reasons.append(
                    f"An independent review flagged these fields as not matching their cited source text: "
                    f"{', '.join(disagreed_required)}"
                )

            text = _load_document_text(db, document)
            strong_call = extract_document(
                client,
                doctype=doctype,
                model=settings.strong_model,
                document_text=text,
                price_in_per_million=settings.strong_model_price_in,
                price_out_per_million=settings.strong_model_price_out,
                prior_issues=reasons,
            )
            strong_extraction = _persist_extraction(db, document, doctype, attempt_no=2, call=strong_call)
            _persist_issues(db, strong_extraction, strong_call.pre_issues)

            strong_rule_issues = doctype.validate_fn(strong_call.record)
            strong_cross_issues = doctype.cross_check_fn(db, document.tenant_id, document.id, strong_call.record)
            _persist_issues(db, strong_extraction, strong_rule_issues + strong_cross_issues)

            strong_grounding = _load_grounding(db, strong_extraction.id)
            strong_values = {f: getattr(strong_call.record, f, None) for f in doctype.all_fields}
            strong_source_texts = {f: strong_call.grounding_hints.get(f, {}).get("source_text") for f in doctype.all_fields}
            strong_review = review_fields(
                client,
                model=settings.cheap_model,  # reviewer always uses the cheap model - see reviewer.py
                fields=doctype.all_fields,
                values=strong_values,
                source_texts=strong_source_texts,
                price_in_per_million=settings.cheap_model_price_in,
                price_out_per_million=settings.cheap_model_price_out,
            )

            final_record = strong_call.record
            final_issues = strong_call.pre_issues + strong_rule_issues + strong_cross_issues
            final_extraction = strong_extraction
            final_grounding = strong_grounding
            final_review = strong_review

        document_score, field_scores = compute_confidence(
            final_record,
            all_fields=doctype.all_fields,
            required_fields=doctype.required_fields,
            issues=final_issues,
            grounding=final_grounding,
            reviewer_agreements=final_review.agreements,
            escalated=escalated,
        )
        for field_name, fs in field_scores.items():
            db.query(FieldValue).filter_by(extraction_id=final_extraction.id, field=field_name).update({"confidence": fs.score})

        document.confidence = document_score
        document.status = (
            DocumentStatus.approved.value if document_score >= settings.confidence_threshold else DocumentStatus.needs_review.value
        )

        if document.status == DocumentStatus.approved.value:
            fire_approved_webhook(settings, document, final_record.model_dump(mode="json"))

        return None


STAGES: dict[str, Stage] = {
    JobStage.parse.value: ParseStage(),
    JobStage.classify.value: ClassifyStage(),
    JobStage.extract.value: ExtractStage(),
    JobStage.validate.value: ValidateStage(),
}


def run_stage(db: Session, document: Document, stage_name: str, settings: Settings, client: genai.Client) -> str | None:
    return STAGES[stage_name].run(db, document, settings, client)
