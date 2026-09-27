"""Deterministic, keyword-based safety checks.

No model call, no cost, and nothing an attacker can talk their way around with
clever phrasing the way they might a model-based classifier - these are fixed
substring denylists, checked at two points in the pipeline:

1. `scan_pdf_bytes` - raw PDF bytes, at upload time, before the file is written
   to disk or parsed. Looks for PDF object/action names that indicate the file
   can execute code or exfiltrate data if it were ever opened in a
   full-featured reader (embedded JavaScript, launch actions, embedded files,
   auto-triggered actions, form submission). This app never renders or
   executes a PDF - PyMuPDF only extracts text/pixels - but a file carrying
   this content should never have been accepted in the first place.

2. `scan_text` - parsed/OCR'd document text, after parsing but before it's
   sent to any LLM or written anywhere. Checks two keyword sets:
     - `PROMPT_INJECTION_KEYWORDS`: text embedded in the "invoice" trying to
       redirect the extraction model's instructions rather than describe a
       purchase.
     - `SQL_INJECTION_KEYWORDS`: classic SQLi payloads (`' OR '1'='1`,
       `UNION SELECT`, `DROP TABLE`, `xp_cmdshell`, `SLEEP(`, ...). Every query
       in this codebase already goes through the SQLAlchemy ORM with bound
       parameters (`filter_by(...)`, `.filter(...)`), so extracted invoice
       text is never concatenated into SQL today - this exists as
       defense-in-depth against a future export/reporting query, dashboard,
       or admin tool that does string-format a field from `extractions.data`
       instead of parameterizing it. Rejecting the payload before it's even
       stored means it can never reach that hypothetical unsafe query later.

Known limitations, worth saying out loud: `scan_pdf_bytes` is a raw byte scan,
so a PDF that hides its object names inside a compressed object stream
(`/ObjStm`, cross-reference streams) could slip past it - a hardened version
would decompress streams first (e.g. via pikepdf/qpdf). Both keyword lists in
`scan_text` are denylists, so they catch known phrasings/payload shapes, not
every possible rewording or encoding (e.g. `UNI/**/ON SELECT`, hex-encoded
payloads). All of this is intentionally simple and fast; it's a first line of
defense, not a replacement for parameterized queries or sandboxing (which this
app already has, via the ORM).
"""
from __future__ import annotations


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
# parsed text, same as PROMPT_INJECTION_KEYWORDS - see module docstring for why
# this matters even though every DB query here already uses bound parameters.
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


def scan_pdf_bytes(content: bytes) -> list[str]:
    """Raw-byte scan for dangerous PDF structure. Call on upload, before the
    file is written to disk or parsed."""
    return [
        f"PDF contains disallowed object '{pattern.decode()}'"
        for pattern in PDF_STRUCTURE_DENYLIST
        if pattern in content
    ]


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
