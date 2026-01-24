"""Tests for tri-model credibility scoring.

This module tests that credibility scoring integrates properly with the tri-model pipeline.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_credibility_scorer_schema():
    """Test that credibility scorer returns expected schema."""
    from tri_model.credibility import score_paper_credibility

    # Mock paper (without API key, will return error result)
    paper = {
        "id": "test123",
        "title": "Test Cancer Detection Paper",
        "source": "Test Journal",
        "venue": "Test Venue",
        "date": "2026-01-01",
        "raw_text": "This is a test abstract about cancer detection.",
    }

    result = score_paper_credibility(paper)

    # Verify result structure
    assert isinstance(result, dict)
    assert "credibility_score" in result
    assert "credibility_reason" in result
    assert "credibility_confidence" in result
    assert "credibility_signals" in result
    assert "scored_at" in result
    assert "scoring_version" in result
    assert "scoring_model" in result

    # Verify types (may be None if API key not available)
    assert result["credibility_score"] is None or isinstance(result["credibility_score"], int)
    assert isinstance(result["credibility_reason"], str)
    assert result["credibility_confidence"] in ["low", "medium", "high"]
    assert isinstance(result["credibility_signals"], dict)

    print("✓ test_credibility_scorer_schema passed")


def test_credibility_result_fields():
    """Test that credibility results have all required fields."""
    from tri_model.credibility import score_paper_credibility

    paper = {
        "id": "test456",
        "title": "Study on Early Detection",
        "source": "PubMed",
        "date": "2025-12-15",
        "raw_text": "Early cancer detection using biomarkers.",
    }

    result = score_paper_credibility(paper)

    # Check required fields exist
    required_fields = [
        "credibility_score",
        "credibility_reason",
        "credibility_confidence",
        "credibility_signals",
        "scored_at",
        "scoring_version",
        "scoring_model",
    ]

    for field in required_fields:
        assert field in result, f"Missing required field: {field}"

    # Check confidence is valid
    assert result["credibility_confidence"] in ["low", "medium", "high"]

    print("✓ test_credibility_result_fields passed")


def test_credibility_graceful_degradation():
    """Test that credibility scoring degrades gracefully without API keys."""
    from tri_model.credibility import score_paper_credibility

    paper = {
        "id": "test789",
        "title": "Biomarker Research",
        "source": "arXiv",
        "date": "2026-01-20",
        "summary": "Research on novel biomarkers.",
    }

    result = score_paper_credibility(paper)

    # Should not crash, should return error result
    assert isinstance(result, dict)
    assert "error" in result or result.get("credibility_score") is None

    # Should still have all required fields
    assert "credibility_reason" in result
    assert "credibility_confidence" in result
    assert "credibility_signals" in result

    print("✓ test_credibility_graceful_degradation passed")


if __name__ == "__main__":
    # Run tests
    test_credibility_scorer_schema()
    test_credibility_result_fields()
    test_credibility_graceful_degradation()
    print("\n✅ All tri-model credibility tests passed!")
