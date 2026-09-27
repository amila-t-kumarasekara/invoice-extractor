from app.pipeline.classify import PageClassification, group_consecutive_pages


def test_group_consecutive_pages_single_group():
    pages = [PageClassification(1, "invoice", 0.9), PageClassification(2, "invoice", 0.85)]
    assert group_consecutive_pages(pages) == [("invoice", [1, 2])]


def test_group_consecutive_pages_splits_on_type_change():
    pages = [
        PageClassification(1, "invoice", 0.9),
        PageClassification(2, "invoice", 0.85),
        PageClassification(3, "unknown", 0.9),
        PageClassification(4, "unknown", 0.9),
    ]
    assert group_consecutive_pages(pages) == [("invoice", [1, 2]), ("unknown", [3, 4])]


def test_group_consecutive_pages_handles_alternating_types():
    pages = [
        PageClassification(1, "invoice", 0.9),
        PageClassification(2, "unknown", 0.9),
        PageClassification(3, "invoice", 0.9),
    ]
    assert group_consecutive_pages(pages) == [("invoice", [1]), ("unknown", [2]), ("invoice", [3])]


def test_group_consecutive_pages_single_page():
    assert group_consecutive_pages([PageClassification(1, "invoice", 0.9)]) == [("invoice", [1])]
