import json

import pytest

from tri_model.json_utils import extract_json_object, normalize_review_json
from tri_model import reviewers
from scripts.score_seed_papers import write_tri_model_events


def test_extract_json_object_from_fenced_block():
    text = """Here is your review:
```json
{"relevancy_score": 80, "relevancy_reason": "ok", "signals": {}, "summary": "s", "concerns": [], "confidence": "high"}
```
"""
    data = extract_json_object(text)
    assert data["relevancy_score"] == 80


def test_extract_json_object_with_extra_text_and_trailing_commas():
    text = """Some preface
{
  "relevancy_score": 90,
  "relevancy_reason": "good",
  "signals": {},
  "summary": "sum",
  "concerns": [],
  "confidence": "medium",
}
Thanks
"""
    data = extract_json_object(text)
    assert data["confidence"] == "medium"


def test_review_parser_missing_fields_reports_missing():
    bad = json.dumps({"relevancy_score": 50, "signals": {}})
    with pytest.raises(ValueError, match="Missing required fields"):
        reviewers._parse_review_json(bad, "v2")


def test_review_parser_type_mismatch_reports_types():
    bad = json.dumps({
        "relevancy_score": "50",
        "relevancy_reason": "ok",
        "signals": [],
        "summary": "s",
        "concerns": "none",
        "confidence": "high",
    })
    with pytest.raises(ValueError, match="Type mismatches"):
        reviewers._parse_review_json(bad, "v2")


def test_review_parser_accepts_valid_schema():
    good = json.dumps({
        "relevancy_score": 70,
        "relevancy_reason": "ok",
        "signals": {"evidence": []},
        "summary": "sum",
        "concerns": [],
        "confidence": "low",
    })
    parsed = reviewers._parse_review_json(good, "v2")
    assert parsed["relevancy_score"] == 70


def test_normalize_review_json_v1_schema():
    raw = {
        "relevancy_score_0_100": 88,
        "relevancy_rating_0_3": 3,
        "key_reasons": ["Reason A", "Reason B"],
        "uncertainty": 0.2,
        "signals": {"evidence": []},
        "summary": "Summary text",
        "concerns": [],
    }
    normalized = normalize_review_json(raw, "v1")
    assert normalized["relevancy_score"] == 88
    assert "Reason A" in normalized["relevancy_reason"]
    assert normalized["confidence"] == "high"


def test_normalize_review_json_concerns_string():
    raw = {
        "relevancy_score_0_100": 80,
        "key_reasons": ["Reason"],
        "uncertainty": 0.5,
        "signals": {},
        "summary": "Summary",
        "concerns": "Some concern",
    }
    normalized = normalize_review_json(raw, "v1")
    assert normalized["concerns"] == ["Some concern"]


def test_normalize_review_json_concerns_missing():
    raw = {
        "relevancy_score_0_100": 80,
        "key_reasons": ["Reason"],
        "uncertainty": 0.5,
        "signals": {},
        "summary": "Summary",
    }
    normalized = normalize_review_json(raw, "v1")
    assert normalized["concerns"] == []


def test_normalize_review_json_concerns_list_cleanup():
    raw = {
        "relevancy_score_0_100": 80,
        "key_reasons": ["Reason"],
        "uncertainty": 0.5,
        "signals": {},
        "summary": "Summary",
        "concerns": ["", "  Concern A  ", None, "Concern B"],
    }
    normalized = normalize_review_json(raw, "v1")
    assert normalized["concerns"] == ["Concern A", "Concern B"]


def test_write_events_from_stub_review(tmp_path):
    results = [
        {
            "publication_id": "pub-1",
            "title": "Paper",
            "source": "test",
            "published_date": "2026-01-01",
            "url": "https://example.com",
            "gpt_evaluation": {
                "evaluation": {
                    "final_relevancy_score": 55,
                    "final_relevancy_reason": "ok",
                    "final_signals": {},
                    "final_summary": "sum",
                    "agreement_level": "moderate",
                    "disagreements": [],
                    "evaluator_rationale": "r",
                    "confidence": "medium",
                }
            },
            "credibility": {},
        }
    ]

    output_path = tmp_path / "tri_model_events.jsonl"
    write_tri_model_events("run-1", "tri-model-benchmark", results, output_path)

    lines = output_path.read_text().strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["final_relevancy_score"] == 55


def test_evaluator_defaults_confidence():
    from tri_model.evaluator import _parse_evaluator_json

    raw = json.dumps({
        "final_relevancy_rating_0_3": 2,
        "final_relevancy_score": 60,
        "final_relevancy_reason": "Reasonable relevance.",
    })

    parsed = _parse_evaluator_json(raw)
    assert parsed["confidence"] == 60


def test_v3_postprocessing_applies_rules(monkeypatch):
    from tri_model.evaluator import _apply_v3_postprocessing

    monkeypatch.setenv("TRI_MODEL_PROMPT_VERSION", "v3")

    paper = {
        "title": "Clinical value of ultrasound in predicting neoadjuvant chemotherapy efficacy for breast cancer",
        "source": "Clinical Oncology",
        "raw_text": "Study predicts pathological response to neoadjuvant chemotherapy in breast cancer.",
        "summary": "",
    }
    parsed = {
        "final_relevancy_rating_0_3": 2,
        "final_relevancy_score": 70,
        "final_relevancy_reason": "Breast cancer imaging relevance",
        "confidence": 70,
    }
    claude_review = {"signals": {"cancer_type": "breast", "early_detection_focus": False}}
    gemini_review = {"signals": {"cancer_type": "breast", "early_detection_focus": False}}

    out = _apply_v3_postprocessing(paper, parsed, claude_review, gemini_review)
    assert out["final_relevancy_score"] <= 20
    assert out["final_relevancy_rating_0_3"] == 0
    assert out.get("final_signals", {}).get("treatment_only") is True


def test_v3_postprocessing_disabled_for_non_v3(monkeypatch):
    from tri_model.evaluator import _apply_v3_postprocessing

    monkeypatch.setenv("TRI_MODEL_PROMPT_VERSION", "v2")
    parsed = {
        "final_relevancy_rating_0_3": 2,
        "final_relevancy_score": 65,
        "final_relevancy_reason": "Keep unchanged",
        "confidence": 70,
    }
    out = _apply_v3_postprocessing({"title": "t", "source": "s", "raw_text": "a"}, parsed.copy(), None, None)
    assert out["final_relevancy_score"] == 65
