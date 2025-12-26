"""Test commercial signal extraction."""

from enrich.commercial import extract_commercial_signals


def test_commercial_extractor_empty_input():
    """Test that extractor returns valid schema for empty input."""
    result = extract_commercial_signals("")

    # Should return valid schema with all fields
    assert "has_sponsor_signal" in result
    assert "sponsor_names" in result
    assert "company_affiliation_signal" in result
    assert "company_names" in result
    assert "evidence_snippets" in result

    # All should be empty/false
    assert result["has_sponsor_signal"] is False
    assert result["sponsor_names"] == []
    assert result["company_affiliation_signal"] is False
    assert result["company_names"] == []
    assert result["evidence_snippets"] == []


def test_commercial_extractor_none_input():
    """Test that extractor handles None input gracefully."""
    result = extract_commercial_signals(None)

    # Should return valid schema
    assert result["has_sponsor_signal"] is False
    assert result["sponsor_names"] == []
    assert result["company_affiliation_signal"] is False
    assert result["company_names"] == []
    assert result["evidence_snippets"] == []


def test_sponsor_signal_detection():
    """Test that sponsor signals are detected."""
    text = "This research was funded by Pfizer Inc and supported by Novartis."

    result = extract_commercial_signals(text)

    assert result["has_sponsor_signal"] is True
    assert len(result["sponsor_names"]) > 0


def test_company_affiliation_detection():
    """Test that company affiliation signals are detected."""
    text = "The author is an employee of Roche Pharmaceuticals."

    result = extract_commercial_signals(text)

    assert result["company_affiliation_signal"] is True
    assert len(result["company_names"]) > 0


def test_no_false_positives_on_clean_text():
    """Test that clean academic text doesn't trigger signals."""
    text = """
    This study investigated the role of protein kinases in cancer progression.
    We used cell culture experiments and found significant results.
    The methodology was rigorous and the conclusions are sound.
    """

    result = extract_commercial_signals(text)

    # Clean text should not trigger commercial signals
    assert result["has_sponsor_signal"] is False
    assert result["company_affiliation_signal"] is False


def test_schema_consistency():
    """Test that schema is consistent across different inputs."""
    inputs = [
        "",
        None,
        "Clean text with no signals",
        "This was funded by Merck",
        "Author is employee of AstraZeneca",
    ]

    for text in inputs:
        result = extract_commercial_signals(text)

        # All should have same keys
        assert set(result.keys()) == {
            "has_sponsor_signal",
            "sponsor_names",
            "company_affiliation_signal",
            "company_names",
            "evidence_snippets",
        }

        # All should have correct types
        assert isinstance(result["has_sponsor_signal"], bool)
        assert isinstance(result["sponsor_names"], list)
        assert isinstance(result["company_affiliation_signal"], bool)
        assert isinstance(result["company_names"], list)
        assert isinstance(result["evidence_snippets"], list)


def test_evidence_snippets_limited():
    """Test that evidence snippets are limited (max 2, max 160 chars each)."""
    text = """
    This research was funded by Company A.
    It was also supported by Company B.
    Additional funding from Company C.
    More support from Company D.
    """

    result = extract_commercial_signals(text)

    # Should have evidence snippets
    assert len(result["evidence_snippets"]) > 0

    # Should be limited to max 2
    assert len(result["evidence_snippets"]) <= 2

    # Each should be <= 160 chars
    for snippet in result["evidence_snippets"]:
        assert len(snippet) <= 160
