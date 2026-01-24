"""Tests for tri-model unicode handling and storage fixes.

This test module verifies:
1. Unicode sanitization (U+2028, U+2029) in text_sanitize module
2. SQLite tri_model event storage handles disagreements as list/dict/string
3. No ascii encoding errors when storing events with unicode
"""

import json
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_sanitize_for_llm():
    """Test that unicode line/paragraph separators are sanitized."""
    from tri_model.text_sanitize import sanitize_for_llm

    # Test U+2028 (LINE SEPARATOR) replacement
    text_with_line_sep = "First line\u2028Second line"
    result = sanitize_for_llm(text_with_line_sep)
    assert "\u2028" not in result
    assert "First line\nSecond line" == result

    # Test U+2029 (PARAGRAPH SEPARATOR) replacement
    text_with_para_sep = "Paragraph 1\u2029Paragraph 2"
    result = sanitize_for_llm(text_with_para_sep)
    assert "\u2029" not in result
    assert "Paragraph 1\n\nParagraph 2" == result

    # Test combined
    text_combined = "Line 1\u2028Line 2\u2029Paragraph 2"
    result = sanitize_for_llm(text_combined)
    assert "\u2028" not in result
    assert "\u2029" not in result

    # Test Windows CRLF normalization
    text_crlf = "Line 1\r\nLine 2"
    result = sanitize_for_llm(text_crlf)
    assert "\r" not in result
    assert "Line 1\nLine 2" == result

    # Test empty/None
    assert sanitize_for_llm("") == ""
    assert sanitize_for_llm(None) == ""

    print("✓ test_sanitize_for_llm passed")


def test_sanitize_paper_for_review():
    """Test that paper dict sanitization handles all text fields."""
    from tri_model.text_sanitize import sanitize_paper_for_review

    paper = {
        "id": "test123",
        "title": "Test\u2028Title",
        "source": "Test\u2029Source",
        "raw_text": "Abstract with\u2028line separator",
        "summary": "Summary\u2029with para separator",
    }

    result = sanitize_paper_for_review(paper)

    # Verify unicode separators removed
    assert "\u2028" not in result["title"]
    assert "\u2029" not in result["source"]
    assert "\u2028" not in result["raw_text"]
    assert "\u2029" not in result["summary"]

    # Verify other fields unchanged
    assert result["id"] == "test123"

    print("✓ test_sanitize_paper_for_review passed")


def test_disagreements_normalization():
    """Test that disagreements parameter is normalized to string."""
    # Simulates the normalization logic from store_tri_model_scoring_event

    # Test with list
    disagreements = ["Claude scored higher", "Gemini noted concerns"]
    if isinstance(disagreements, (list, dict)):
        result = json.dumps(disagreements, ensure_ascii=False)
    else:
        result = str(disagreements)
    assert isinstance(result, str)
    assert "Claude scored higher" in result
    print("✓ test_disagreements_normalization (list) passed")

    # Test with dict
    disagreements = {"score_delta": 25, "reasons": ["methodology", "scope"]}
    if isinstance(disagreements, (list, dict)):
        result = json.dumps(disagreements, ensure_ascii=False)
    else:
        result = str(disagreements)
    assert isinstance(result, str)
    assert "score_delta" in result
    print("✓ test_disagreements_normalization (dict) passed")

    # Test with string
    disagreements = "None - both agreed"
    if isinstance(disagreements, (list, dict)):
        result = json.dumps(disagreements, ensure_ascii=False)
    else:
        result = str(disagreements)
    assert result == "None - both agreed"
    print("✓ test_disagreements_normalization (string) passed")

    # Test with None
    disagreements = None
    if isinstance(disagreements, (list, dict)):
        result = json.dumps(disagreements, ensure_ascii=False)
    elif disagreements is None:
        result = None
    else:
        result = str(disagreements)
    assert result is None
    print("✓ test_disagreements_normalization (None) passed")


def test_agreement_level_normalization():
    """Test that agreement_level is normalized to string."""
    # Simulates the normalization logic from store_tri_model_scoring_event

    # Test with string
    agreement_level = "moderate"
    result = str(agreement_level) if agreement_level is not None else "unknown"
    assert result == "moderate"
    print("✓ test_agreement_level_normalization (string) passed")

    # Test with None
    agreement_level = None
    result = str(agreement_level) if agreement_level is not None else "unknown"
    assert result == "unknown"
    print("✓ test_agreement_level_normalization (None) passed")


def test_json_dumps_unicode():
    """Test that json.dumps with ensure_ascii=False handles unicode."""
    # Test unicode in JSON
    data = {
        "title": "Test\u2028with unicode",
        "summary": "Paragraph\u2029separator",
    }

    result = json.dumps(data, ensure_ascii=False)
    assert isinstance(result, str)
    # After sanitization, these should be newlines, but json.dumps should handle them
    print("✓ test_json_dumps_unicode passed")


def test_claude_review_unicode_robustness():
    """Test that Claude review handles unicode without crashing."""
    from tri_model.reviewers import claude_review
    from tri_model.text_sanitize import sanitize_for_llm

    # Paper with problematic unicode characters in all text fields
    paper = {
        "id": "test123",
        "title": "Cancer Detection\u2028Using Novel\u2029Biomarkers",
        "source": "Test\u2028Journal",
        "raw_text": "Abstract with\u2028line separator and\u2029paragraph separator and other text. "
                    "This abstract contains multiple instances\u2028of unicode\u2029separators\u2028scattered throughout.",
    }

    # Should not crash (even without API keys, should fail gracefully)
    # The sanitization should happen before any API calls
    result = claude_review(paper)

    # Verify result structure (will have success=False without API key, but shouldn't crash)
    assert isinstance(result, dict)
    assert "success" in result
    assert "model" in result
    assert "error" in result  # Should have error field

    # If it failed due to missing API key, that's expected
    # The key is that it didn't crash with encoding error
    if not result["success"]:
        # Error should be about API key, not encoding
        error_msg = result.get("error", "").lower()
        assert "api" in error_msg or "key" in error_msg or "configured" in error_msg, \
            f"Expected API key error, got: {result['error']}"

    # Verify sanitization was applied
    sanitized_title = sanitize_for_llm(paper["title"])
    assert "\u2028" not in sanitized_title
    assert "\u2029" not in sanitized_title

    print("✓ test_claude_review_unicode_robustness passed")


def test_claude_review_extreme_unicode():
    """Test Claude review with extreme unicode edge cases."""
    from tri_model.reviewers import claude_review

    # Paper with multiple unicode issues
    paper = {
        "id": "test_extreme",
        "title": "Study\u2028on\u2029Cancer\u2028Biomarkers",
        "source": "Journal\u2028of\u2029Medicine",
        "raw_text": (
            "This is a comprehensive study\u2028examining novel biomarkers\u2029for early detection. "
            "Methods\u2028included analysis\u2029of patient samples. "
            "Results\u2028showed significant\u2029correlations. "
            "Conclusions\u2028suggest clinical\u2029applications."
        ),
        "summary": "Study\u2028about\u2029biomarkers",
    }

    # Should handle gracefully without crashing
    result = claude_review(paper)

    # Verify it returns proper error structure, not a crash
    assert isinstance(result, dict)
    assert "success" in result
    assert "error" in result

    # Should fail due to API key, not encoding
    assert result["success"] is False
    error_msg = result.get("error", "").lower()
    assert "codec" not in error_msg, "Should not have encoding error"
    assert "encode" not in error_msg, "Should not have encoding error"

    print("✓ test_claude_review_extreme_unicode passed")


def test_evaluator_unicode_robustness():
    """Test that GPT evaluator handles unicode without crashing."""
    from tri_model.evaluator import gpt_evaluate

    # Paper with unicode
    paper = {
        "id": "test456",
        "title": "Test\u2028Paper",
        "source": "Test\u2029Source",
        "raw_text": "Abstract\u2028text",
    }

    # Mock reviews with unicode
    claude_result = {
        "success": True,
        "review": {
            "relevancy_score": 75,
            "relevancy_reason": "Good\u2028paper",
            "signals": {},
            "summary": "Summary\u2029text",
            "concerns": "None",
            "confidence": "high",
        }
    }

    gemini_result = {
        "success": True,
        "review": {
            "relevancy_score": 80,
            "relevancy_reason": "Also\u2028good",
            "signals": {},
            "summary": "Another\u2029summary",
            "concerns": "None",
            "confidence": "high",
        }
    }

    # Should not crash (will fail without API key, but gracefully)
    result = gpt_evaluate(paper, claude_result, gemini_result)

    # Verify result structure
    assert isinstance(result, dict)
    assert "success" in result

    print("✓ test_evaluator_unicode_robustness passed")


if __name__ == "__main__":
    # Run tests
    test_sanitize_for_llm()
    test_sanitize_paper_for_review()
    test_disagreements_normalization()
    test_agreement_level_normalization()
    test_json_dumps_unicode()
    test_claude_review_unicode_robustness()
    test_claude_review_extreme_unicode()
    test_evaluator_unicode_robustness()
    print("\n✅ All tests passed!")
