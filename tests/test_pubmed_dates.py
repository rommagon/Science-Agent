"""Test PubMed date parsing functionality."""

from datetime import datetime

from ingest.fetch import _parse_pubmed_date


def test_parse_yyyy_format():
    """Test parsing YYYY format (e.g., '2025')."""
    result, missing = _parse_pubmed_date("2025")

    assert missing is False
    assert result is not None
    assert result.year == 2025
    assert result.month == 1  # Should default to January
    assert result.day == 1


def test_parse_yyyy_mon_format():
    """Test parsing YYYY Mon format (e.g., '2025 Nov')."""
    result, missing = _parse_pubmed_date("2025 Nov")

    assert missing is False
    assert result is not None
    assert result.year == 2025
    assert result.month == 11  # November
    assert result.day == 1  # Should default to 1st of month


def test_parse_yyyy_mon_full_name():
    """Test parsing with full month name (e.g., '2025 November')."""
    result, missing = _parse_pubmed_date("2025 November")

    assert missing is False
    assert result is not None
    assert result.year == 2025
    assert result.month == 11


def test_parse_yyyy_mon_dd_format():
    """Test parsing YYYY Mon DD format (e.g., '2025 Nov 15')."""
    result, missing = _parse_pubmed_date("2025 Nov 15")

    assert missing is False
    assert result is not None
    assert result.year == 2025
    assert result.month == 11
    assert result.day == 15


def test_parse_yyyy_mon_range():
    """Test parsing YYYY Mon-Mon format (e.g., '2025 Nov-Dec')."""
    # Should parse first month from range
    result, missing = _parse_pubmed_date("2025 Nov-Dec")

    assert missing is False
    assert result is not None
    assert result.year == 2025
    assert result.month == 11  # Should use first month (Nov)


def test_parse_winter_season():
    """Test parsing Winter season (e.g., '2025 Winter')."""
    result, missing = _parse_pubmed_date("2025 Winter")

    assert missing is False
    assert result is not None
    assert result.year == 2025
    assert result.month == 1  # Winter maps to January


def test_parse_spring_season():
    """Test parsing Spring season."""
    result, missing = _parse_pubmed_date("2025 Spring")

    assert missing is False
    assert result is not None
    assert result.year == 2025
    assert result.month == 4  # Spring maps to April


def test_parse_summer_season():
    """Test parsing Summer season."""
    result, missing = _parse_pubmed_date("2025 Summer")

    assert missing is False
    assert result is not None
    assert result.year == 2025
    assert result.month == 7  # Summer maps to July


def test_parse_fall_season():
    """Test parsing Fall/Autumn season."""
    result, missing = _parse_pubmed_date("2025 Fall")

    assert missing is False
    assert result is not None
    assert result.year == 2025
    assert result.month == 10  # Fall maps to October


def test_parse_empty_string():
    """Test that empty string returns None with missing flag."""
    result, missing = _parse_pubmed_date("")

    assert missing is True
    assert result is None


def test_parse_none():
    """Test that None returns None with missing flag."""
    result, missing = _parse_pubmed_date(None)

    assert missing is True
    assert result is None


def test_parse_invalid_format():
    """Test that invalid format returns None with missing flag."""
    result, missing = _parse_pubmed_date("invalid-date-format")

    assert missing is True
    assert result is None


def test_parse_slash_format():
    """Test parsing YYYY/MM/DD format."""
    result, missing = _parse_pubmed_date("2025/11/15")

    assert missing is False
    assert result is not None
    assert result.year == 2025
    assert result.month == 11
    assert result.day == 15


def test_parse_case_insensitive():
    """Test that month names are case-insensitive."""
    test_cases = [
        "2025 nov",
        "2025 NOV",
        "2025 Nov",
        "2025 nOv",
    ]

    for date_str in test_cases:
        result, missing = _parse_pubmed_date(date_str)
        assert missing is False
        assert result is not None
        assert result.month == 11
