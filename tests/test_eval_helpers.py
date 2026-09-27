import json
from datetime import date

from evals.run_eval import load_samples, values_match


def test_values_match_treats_none_as_none():
    assert values_match(None, None) is True
    assert values_match(None, "something") is False


def test_values_match_compares_dates_by_isoformat():
    assert values_match("2026-01-15", date(2026, 1, 15)) is True
    assert values_match("2026-01-15", date(2026, 1, 16)) is False


def test_values_match_tolerates_float_rounding():
    assert values_match(100.0, 100.004) is True
    assert values_match(100.0, 101.0) is False


def test_values_match_is_case_insensitive_for_strings():
    assert values_match("Acme Co", "acme co") is True


def test_load_samples_skips_non_invoice_labels(tmp_path):
    (tmp_path / "real.json").write_text(json.dumps({"supplier": "Acme"}))
    (tmp_path / "real.pdf").write_bytes(b"%PDF-1.4\n%%EOF")

    (tmp_path / "letter.json").write_text(json.dumps({"doc_type": "non_invoice"}))
    (tmp_path / "letter.pdf").write_bytes(b"%PDF-1.4\n%%EOF")

    samples = load_samples(tmp_path)
    names = [p.stem for p, _ in samples]
    assert names == ["real"]


def test_load_samples_skips_json_with_no_matching_pdf(tmp_path):
    (tmp_path / "orphan.json").write_text(json.dumps({"supplier": "Acme"}))

    assert load_samples(tmp_path) == []
