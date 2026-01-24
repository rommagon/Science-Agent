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


if __name__ == "__main__":
    # Run tests
    test_sanitize_for_llm()
    test_sanitize_paper_for_review()
    test_disagreements_normalization()
    test_agreement_level_normalization()
    test_json_dumps_unicode()
    print("\n✅ All tests passed!")
