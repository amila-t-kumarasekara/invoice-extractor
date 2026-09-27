import shutil

import fitz
import pytest

from app.pipeline.parse import parse_pdf


def make_text_pdf(path: str, lines: list[str]) -> None:
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line)
        y += 20
    doc.save(path)
    doc.close()


def make_blank_pdf(path: str) -> None:
    doc = fitz.open()
    doc.new_page()
    doc.save(path)
    doc.close()


def test_parse_uses_text_layer_when_present(tmp_path):
    pdf_path = tmp_path / "invoice.pdf"
    make_text_pdf(str(pdf_path), ["Invoice #INV-1", "Total: 110.00 USD"])

    parsed = parse_pdf(str(pdf_path))

    assert parsed.page_count == 1
    assert parsed.pages[0].source == "text_layer"
    assert not parsed.used_ocr
    assert "INV-1" in parsed.pages[0].text


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_parse_falls_back_to_ocr_on_blank_text_layer(tmp_path):
    pdf_path = tmp_path / "scanned.pdf"
    make_blank_pdf(str(pdf_path))

    parsed = parse_pdf(str(pdf_path))

    assert parsed.pages[0].source == "ocr"
    assert parsed.used_ocr
