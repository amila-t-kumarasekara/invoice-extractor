"""Deterministic grounding (NEW_PLAN.md Phase 6): map a model-reported
`source_text` to a bounding box on a page, by actually searching that page's
word boxes for it - never by trusting coordinates from the model.

The model has no way to know pixel positions; it only ever sees the page's
plain text, so any bounding box it claimed would be fabricated. Its
`page_no`/`source_text` are just hints for where to look; this module is the
actual source of truth for "does this text really appear there, and where."
A field whose hint doesn't resolve to a real match is *not grounded* - that's
a meaningful, free signal (no model call) that feeds the confidence gate.
"""
from __future__ import annotations

import re


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def find_bbox(words: list[dict], target_text: str, max_window: int = 8) -> list[float] | None:
    """`words` is a list of {"text": str, "bbox": [x0, y0, x1, y1]} in reading
    order. Returns the union bbox of the shortest contiguous run of words
    whose concatenation (normalized) equals `target_text`, or None."""
    target_norm = _normalize(target_text) if target_text else ""
    if not target_norm or not words:
        return None

    n = len(words)
    for start in range(n):
        concatenated = ""
        for window in range(1, min(max_window, n - start) + 1):
            word_text = words[start + window - 1]["text"]
            concatenated = f"{concatenated} {word_text}".strip() if concatenated else word_text
            if _normalize(concatenated) == target_norm:
                boxes = [words[i]["bbox"] for i in range(start, start + window)]
                return [
                    min(b[0] for b in boxes),
                    min(b[1] for b in boxes),
                    max(b[2] for b in boxes),
                    max(b[3] for b in boxes),
                ]
            if len(_normalize(concatenated)) > len(target_norm) + 20:
                break  # this window has drifted well past the target length; stop extending it
    return None


def ground_field(page_words_by_no: dict[int, list[dict]], page_no: int | None, source_text: str | None) -> dict | None:
    """Returns {"page_no", "bbox", "source_text"} or None if the field can't
    be grounded (missing hint, unknown page, or the text isn't actually on
    that page)."""
    if page_no is None or not source_text:
        return None
    words = page_words_by_no.get(page_no)
    if not words:
        return None
    bbox = find_bbox(words, source_text)
    if bbox is None:
        return None
    return {"page_no": page_no, "bbox": bbox, "source_text": source_text}
