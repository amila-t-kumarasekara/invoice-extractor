"""Deterministic safety checks - file-level and content-level. No model call,
no cost, and nothing an attacker can talk their way around with clever
phrasing the way they might a model-based classifier.

Two stages:

1. `scan_upload` - runs at upload time, before the file is written to storage
   or a job is created:
     - real file type sniffed from content (magic bytes via `filetype`), not
       the filename extension - a `.pdf`-named `.exe` is rejected
     - size limit, page-count limit
     - encrypted or corrupt/unopenable PDFs are rejected
     - `PDF_STRUCTURE_DENYLIST`: PDF object/action names that indicate the
       file can execute code or exfiltrate data if it were ever opened in a
       full-featured reader (embedded JavaScript, launch actions, embedded
       files, auto-triggered actions, form submission). This app never
       renders or executes a PDF - PyMuPDF only extracts text/pixels - but a
       file carrying this content should never have been accepted at all.

2. `scan_text` - parsed/OCR'd document text, after parsing but before it's
   sent to any LLM or persisted anywhere. Checks two keyword sets:
     - `PROMPT_INJECTION_KEYWORDS`: text embedded in the "invoice" trying to
       redirect the extraction model's instructions rather than describe a
       purchase.
     - `SQL_INJECTION_KEYWORDS`: classic SQLi payloads (`' OR '1'='1`,
       `UNION SELECT`, `DROP TABLE`, `xp_cmdshell`, `SLEEP(`, ...). Every query
       in this codebase goes through the SQLAlchemy ORM with bound
       parameters, so there's no live SQL-injection path today - this exists
       as defense-in-depth against a future export/reporting query that
       string-formats a field instead of parameterizing it.

Known limitations, worth saying out loud: the PDF structure scan is a raw byte
scan, so a PDF that hides its object names inside a compressed object stream
(`/ObjStm`, cross-reference streams) could slip past it - a hardened version
would decompress streams first (e.g. via pikepdf/qpdf). Both keyword lists in
`scan_text` are denylists: they catch known phrasings/payload shapes, not
every possible rewording or encoding. All of this is intentionally simple and
fast; it's a first line of defense, not a replacement for parameterized
queries or sandboxing (which this app already has, via the ORM).
"""
from __future__ import annotations

import filetype
import fitz


class SecurityRejection(Exception):
    """Raised when a document fails a deterministic safety check."""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


# PDF object/action names that indicate executable or data-exfiltrating
# content. Matched as raw byte substrings against the uploaded file.
PDF_STRUCTURE_DENYLIST: tuple[bytes, ...] = (
    b"/JavaScript",
    b"/JS",
    b"/Launch",
    b"/EmbeddedFile",
    b"/OpenAction",
    b"/AA",
    b"/SubmitForm",
    b"/ImportData",
    b"/RichMedia",
    b"/GoToR",
)

# Phrasing associated with prompt-injection attempts against document
# extraction pipelines. Matched case-insensitively against parsed text.
PROMPT_INJECTION_KEYWORDS: tuple[str, ...] = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "ignore the above instructions",
    "disregard previous instructions",
    "disregard all prior instructions",
    "disregard the above",
    "new instructions:",
    "system prompt",
    "reveal your instructions",
    "reveal your system prompt",
    "print your instructions",
    "override your instructions",
    "forget your instructions",
    "forget everything above",
    "do anything now",
    "jailbreak",
    "<system>",
    "</system>",
    "[system]",
)

# Classic SQL-injection payload shapes. Matched case-insensitively against
# parsed text, same as PROMPT_INJECTION_KEYWORDS.
SQL_INJECTION_KEYWORDS: tuple[str, ...] = (
    "' or '1'='1",
    '" or "1"="1',
    "' or 1=1",
    "' or 'a'='a",
    "or 1=1--",
    "'; drop table",
    "drop table",
    "union select",
    "select * from",
    "insert into",
    "delete from",
    "xp_cmdshell",
    "exec(",
    "execute immediate",
    "waitfor delay",
    "sleep(",
    "benchmark(",
    "pg_sleep(",
    "information_schema.tables",
    "'; --",
    "' --",
    "/*!",
)


def sniff_mime_type(content: bytes) -> str | None:
    kind = filetype.guess(content)
    return kind.mime if kind else None


def scan_pdf_structure(content: bytes) -> list[str]:
    """Raw-byte scan for dangerous PDF structure."""
    return [
        f"PDF contains disallowed object '{pattern.decode()}'"
        for pattern in PDF_STRUCTURE_DENYLIST
        if pattern in content
    ]


def scan_upload(content: bytes, *, max_bytes: int, max_pages: int, allowed_mime_types: tuple[str, ...]) -> list[str]:
    """Full upload-time safety check: size, real content type, page count,
    encryption, and PDF structure denylist. Call before storing the file or
    creating a document/job row."""
    issues: list[str] = []

    if len(content) > max_bytes:
        issues.append(f"file exceeds the {max_bytes // (1024 * 1024)}MB size limit")

    mime = sniff_mime_type(content)
    if mime not in allowed_mime_types:
        issues.append(f"file content type '{mime or 'unknown'}' is not allowed")
        return issues  # no point inspecting PDF structure of a non-PDF/unrecognized file

    if mime == "application/pdf":
        try:
            doc = fitz.open(stream=content, filetype="pdf")
        except Exception as exc:  # noqa: BLE001 - any failure to open means "reject", not "crash"
            issues.append(f"file could not be opened as a valid PDF: {exc}")
            return issues
        try:
            if doc.is_encrypted:
                issues.append("PDF is encrypted/password-protected")
            elif doc.page_count > max_pages:
                issues.append(f"PDF has {doc.page_count} pages, exceeding the limit of {max_pages}")
        finally:
            doc.close()

    issues += scan_pdf_structure(content)
    return issues


def scan_text(text: str) -> list[str]:
    """Keyword scan for prompt-injection and SQL-injection payloads in
    parsed/OCR'd text. Call after parsing, before the text is sent to an LLM
    or persisted anywhere."""
    lowered = text.lower()
    hits = [
        f"document text contains suspicious phrase: '{phrase}'"
        for phrase in PROMPT_INJECTION_KEYWORDS
        if phrase in lowered
    ]
    hits += [
        f"document text contains a SQL-injection-style pattern: '{phrase}'"
        for phrase in SQL_INJECTION_KEYWORDS
        if phrase in lowered
    ]
    return hits
