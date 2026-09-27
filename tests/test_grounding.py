from app.pipeline.grounding import find_bbox, ground_field

WORDS = [
    {"text": "Invoice", "bbox": [10, 10, 50, 20]},
    {"text": "#INV-1001", "bbox": [55, 10, 100, 20]},
    {"text": "Supplier:", "bbox": [10, 30, 60, 40]},
    {"text": "Acme", "bbox": [65, 30, 90, 40]},
    {"text": "Co", "bbox": [95, 30, 110, 40]},
]


def test_find_bbox_single_word():
    assert find_bbox(WORDS, "Supplier:") == [10, 30, 60, 40]


def test_find_bbox_multi_word_union():
    assert find_bbox(WORDS, "Acme Co") == [65, 30, 110, 40]


def test_find_bbox_is_case_and_whitespace_insensitive():
    assert find_bbox(WORDS, "  acme   CO  ") == [65, 30, 110, 40]


def test_find_bbox_returns_none_when_not_present():
    assert find_bbox(WORDS, "Nonexistent Text") is None


def test_find_bbox_returns_none_for_empty_input():
    assert find_bbox(WORDS, "") is None
    assert find_bbox([], "Acme") is None


def test_ground_field_success():
    result = ground_field({1: WORDS}, 1, "Acme Co")
    assert result == {"page_no": 1, "bbox": [65, 30, 110, 40], "source_text": "Acme Co"}


def test_ground_field_fails_on_wrong_page_number():
    assert ground_field({1: WORDS}, 2, "Acme Co") is None


def test_ground_field_fails_when_hints_missing():
    assert ground_field({1: WORDS}, None, None) is None
    assert ground_field({1: WORDS}, 1, None) is None


def test_ground_field_fails_when_source_text_is_hallucinated():
    # The model claims text that simply isn't on the page - this is exactly
    # the "self-consistent hallucination" case grounding exists to catch.
    assert ground_field({1: WORDS}, 1, "Totally Made Up Company") is None
