"""Parse a PDF into plain text, word-level bounding boxes, and a rendered page
image per page.

Strategy: try the PDF's embedded text layer first (free, exact, no API call).
Only fall back to Tesseract OCR on pages where the text layer is empty or too
sparse to be real text (i.e. scanned/photographed pages). This is the "use AI
only where it adds value" step - most digitally-generated invoices never need
OCR or an LLM call just to get raw text out.

Every page is also rendered to a PNG at `IMAGE_DPI`, and word boxes (from
either the text layer or Tesseract) are expressed in that *same* image's pixel
space - text-layer coordinates come out of PyMuPDF in PDF points (72 dpi) and
are scaled up to match; Tesseract's `image_to_data` already reports pixel
coordinates against the image it was given, which is the same rendered image.
Keeping both sources in one coordinate space means the review UI can draw a
bounding box directly over the stored image with no per-source scaling logic.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

IMAGE_DPI = 200
PDF_POINTS_DPI = 72
_SCALE = IMAGE_DPI / PDF_POINTS_DPI


@dataclass
class WordBox:
    text: str
    bbox: list[float]  # [x0, y0, x1, y1] in the rendered page image's pixel space

    def to_json(self) -> dict:
        return {"text": self.text, "bbox": self.bbox}


@dataclass
class PageText:
    page_number: int
    text: str
    source: str  # "text_layer" | "ocr"
    words: list[WordBox]
    image_bytes: bytes


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


def _text_layer_words(page: fitz.Page) -> list[WordBox]:
    raw_words = sorted(page.get_text("words"), key=lambda w: (w[5], w[6], w[7]))  # block, line, word_no
    return [
        WordBox(text=w[4], bbox=[w[0] * _SCALE, w[1] * _SCALE, w[2] * _SCALE, w[3] * _SCALE])
        for w in raw_words
    ]


def _ocr_words(image: Image.Image) -> list[WordBox]:
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    words = []
    for i in range(len(data["text"])):
        text = data["text"][i].strip()
        if not text:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        words.append(WordBox(text=text, bbox=[float(x), float(y), float(x + w), float(y + h)]))
    return words


def parse_pdf(path: str, min_chars_per_page: int = 20) -> ParsedDocument:
    doc = fitz.open(path)
    try:
        pages: list[PageText] = []
        for index, page in enumerate(doc):
            pixmap = page.get_pixmap(dpi=IMAGE_DPI)
            image_bytes = pixmap.tobytes("png")

            text = page.get_text().strip()
            if len(text) >= min_chars_per_page:
                words = _text_layer_words(page)
                pages.append(
                    PageText(page_number=index + 1, text=text, source="text_layer", words=words, image_bytes=image_bytes)
                )
            else:
                pil_image = Image.open(io.BytesIO(image_bytes))
                ocr_text = pytesseract.image_to_string(pil_image).strip()
                words = _ocr_words(pil_image)
                pages.append(
                    PageText(page_number=index + 1, text=ocr_text, source="ocr", words=words, image_bytes=image_bytes)
                )
        return ParsedDocument(pages=pages)
    finally:
        doc.close()
