"""Parse a PDF into plain text.

Strategy: try the PDF's embedded text layer first (free, exact, no API call).
Only fall back to Tesseract OCR on pages where the text layer is empty or too
sparse to be real text (i.e. scanned/photographed pages). This is the "use AI
only where it adds value" step - most digitally-generated invoices never need
OCR or an LLM call just to get raw text out.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import fitz  # PyMuPDF
import pytesseract
from PIL import Image


@dataclass
class PageText:
    page_number: int
    text: str
    source: str  # "text_layer" | "ocr"


@dataclass
class ParsedDocument:
    pages: list[PageText] = field(default_factory=list)

    @property
    def used_ocr(self) -> bool:
        return any(p.source == "ocr" for p in self.pages)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def as_prompt_text(self) -> str:
        parts = []
        for page in self.pages:
            parts.append(f"--- Page {page.page_number} ({page.source}) ---\n{page.text}")
        return "\n\n".join(parts)


def _ocr_page(page: fitz.Page) -> str:
    pix = page.get_pixmap(dpi=300)
    image = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(image)


def parse_pdf(path: str, min_chars_per_page: int = 20) -> ParsedDocument:
    doc = fitz.open(path)
    try:
        pages: list[PageText] = []
        for index, page in enumerate(doc):
            text = page.get_text().strip()
            if len(text) >= min_chars_per_page:
                pages.append(PageText(page_number=index + 1, text=text, source="text_layer"))
            else:
                ocr_text = _ocr_page(page).strip()
                pages.append(PageText(page_number=index + 1, text=ocr_text, source="ocr"))
        return ParsedDocument(pages=pages)
    finally:
        doc.close()
