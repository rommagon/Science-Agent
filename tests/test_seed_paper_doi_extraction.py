#!/usr/bin/env python3
"""Unit tests for DOI extraction from HTML in score_seed_papers.py.

These tests verify that DOI extraction works correctly from various
HTML structures including meta tags and JSON-LD.
"""

import json
import sys
from pathlib import Path

import pytest

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.score_seed_papers import (
    extract_doi_from_html,
    extract_metadata_from_html,
    extract_doi_from_url,
    extract_pmid_from_url,
    _parse_date_string,
    _extract_doi_from_jsonld,
)


# Path to test fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestExtractDoiFromUrl:
    """Tests for extract_doi_from_url function."""

    def test_doi_org_url(self):
        """Test extracting DOI from doi.org URL."""
        url = "https://doi.org/10.1038/s41586-024-07051-0"
        assert extract_doi_from_url(url) == "10.1038/s41586-024-07051-0"

    def test_dx_doi_org_url(self):
        """Test extracting DOI from dx.doi.org URL."""
        url = "https://dx.doi.org/10.1038/s41586-024-07051-0"
        assert extract_doi_from_url(url) == "10.1038/s41586-024-07051-0"

    def test_biorxiv_url(self):
        """Test extracting DOI from bioRxiv URL."""
        url = "https://www.biorxiv.org/content/10.1101/2024.01.15.575123v1"
        assert extract_doi_from_url(url) == "10.1101/2024.01.15.575123v1"

    def test_medrxiv_url(self):
        """Test extracting DOI from medRxiv URL."""
        url = "https://www.medrxiv.org/content/10.1101/2024.02.20.24303012v1"
        assert extract_doi_from_url(url) == "10.1101/2024.02.20.24303012v1"

    def test_nature_article_url_without_doi(self):
        """Test that Nature article URL without DOI returns None."""
        url = "https://www.nature.com/articles/s41586-024-07051-0"
        # This URL doesn't have DOI in path (s41586... is article ID, not DOI)
        # The function should return None since it doesn't match DOI pattern
        result = extract_doi_from_url(url)
        # Actually, our pattern DOES match this since it looks for 10.XXXX
        # Let me check... the pattern nature\.com/articles/(10\.\d{4,}/...)
        # won't match s41586-024-07051-0 since it doesn't start with 10.
        assert result is None

    def test_url_with_trailing_punctuation(self):
        """Test that trailing punctuation is removed from DOI."""
        url = "https://doi.org/10.1038/s41586-024-07051-0."
        assert extract_doi_from_url(url) == "10.1038/s41586-024-07051-0"


class TestExtractPmidFromUrl:
    """Tests for extract_pmid_from_url function."""

    def test_pubmed_url(self):
        """Test extracting PMID from PubMed URL."""
        url = "https://pubmed.ncbi.nlm.nih.gov/39385123/"
        assert extract_pmid_from_url(url) == "39385123"

    def test_ncbi_pubmed_url(self):
        """Test extracting PMID from NCBI PubMed URL."""
        url = "https://www.ncbi.nlm.nih.gov/pubmed/39385123"
        assert extract_pmid_from_url(url) == "39385123"

    def test_non_pubmed_url(self):
        """Test that non-PubMed URL returns None."""
        url = "https://www.nature.com/articles/s41586-024-07051-0"
        assert extract_pmid_from_url(url) is None


class TestExtractDoiFromHtml:
    """Tests for extract_doi_from_html function."""

    def test_nature_article_fixture(self):
        """Test DOI extraction from Nature article HTML fixture."""
        html_path = FIXTURES_DIR / "nature_article_sample.html"
        if not html_path.exists():
            pytest.skip("Nature article fixture not found")

        html = html_path.read_text()
        doi = extract_doi_from_html(html)
        assert doi == "10.1038/s41591-024-03001-0"

    def test_citation_doi_meta_tag(self):
        """Test DOI extraction from citation_doi meta tag."""
        html = '''
        <html>
        <head>
            <meta name="citation_doi" content="10.1038/s41586-024-07051-0">
        </head>
        </html>
        '''
        doi = extract_doi_from_html(html)
        assert doi == "10.1038/s41586-024-07051-0"

    def test_citation_doi_meta_tag_reversed_attrs(self):
        """Test DOI extraction when meta tag attributes are reversed."""
        html = '''
        <html>
        <head>
            <meta content="10.1038/s41586-024-07051-0" name="citation_doi">
        </head>
        </html>
        '''
        doi = extract_doi_from_html(html)
        assert doi == "10.1038/s41586-024-07051-0"

    def test_dc_identifier_meta_tag(self):
        """Test DOI extraction from dc.identifier meta tag."""
        html = '''
        <html>
        <head>
            <meta name="dc.identifier" content="doi:10.1038/s41586-024-07051-0">
        </head>
        </html>
        '''
        doi = extract_doi_from_html(html)
        assert doi == "10.1038/s41586-024-07051-0"

    def test_og_url_with_doi(self):
        """Test DOI extraction from og:url containing doi.org."""
        html = '''
        <html>
        <head>
            <meta property="og:url" content="https://doi.org/10.1038/s41586-024-07051-0">
        </head>
        </html>
        '''
        doi = extract_doi_from_html(html)
        assert doi == "10.1038/s41586-024-07051-0"

    def test_doi_org_href(self):
        """Test DOI extraction from href containing doi.org."""
        html = '''
        <html>
        <body>
            <a href="https://doi.org/10.1038/s41586-024-07051-0">DOI</a>
        </body>
        </html>
        '''
        doi = extract_doi_from_html(html)
        assert doi == "10.1038/s41586-024-07051-0"

    def test_json_ld_doi(self):
        """Test DOI extraction from JSON-LD script."""
        html = '''
        <html>
        <head>
            <script type="application/ld+json">
            {
                "@type": "ScholarlyArticle",
                "identifier": {
                    "@type": "PropertyValue",
                    "propertyID": "doi",
                    "value": "10.1038/s41586-024-07051-0"
                }
            }
            </script>
        </head>
        </html>
        '''
        doi = extract_doi_from_html(html)
        assert doi == "10.1038/s41586-024-07051-0"

    def test_json_ld_doi_in_array(self):
        """Test DOI extraction from JSON-LD array."""
        html = '''
        <html>
        <head>
            <script type="application/ld+json">
            [{
                "@type": "WebPage"
            }, {
                "@type": "ScholarlyArticle",
                "doi": "10.1038/s41586-024-07051-0"
            }]
            </script>
        </head>
        </html>
        '''
        doi = extract_doi_from_html(html)
        assert doi == "10.1038/s41586-024-07051-0"

    def test_no_doi_in_html(self):
        """Test that HTML without DOI returns None."""
        html = '''
        <html>
        <head>
            <title>Some Article</title>
        </head>
        </html>
        '''
        doi = extract_doi_from_html(html)
        assert doi is None

    def test_minimal_article_fixture(self):
        """Test DOI extraction from minimal article (should return None)."""
        html_path = FIXTURES_DIR / "minimal_article.html"
        if not html_path.exists():
            pytest.skip("Minimal article fixture not found")

        html = html_path.read_text()
        doi = extract_doi_from_html(html)
        assert doi is None


class TestExtractMetadataFromHtml:
    """Tests for extract_metadata_from_html function."""

    def test_nature_article_fixture(self):
        """Test metadata extraction from Nature article HTML fixture."""
        html_path = FIXTURES_DIR / "nature_article_sample.html"
        if not html_path.exists():
            pytest.skip("Nature article fixture not found")

        html = html_path.read_text()
        metadata = extract_metadata_from_html(html)

        assert metadata["title"] == "Early detection of cancer using blood-based biomarkers"
        assert metadata["source"] == "Nature Medicine"
        assert metadata["published_date"] is not None
        assert "2024-03-15" in metadata["published_date"]

    def test_og_title_extraction(self):
        """Test title extraction from og:title."""
        html = '''
        <html>
        <head>
            <meta property="og:title" content="Test Article Title">
        </head>
        </html>
        '''
        metadata = extract_metadata_from_html(html)
        assert metadata["title"] == "Test Article Title"

    def test_citation_title_extraction(self):
        """Test title extraction from citation_title."""
        html = '''
        <html>
        <head>
            <meta name="citation_title" content="Citation Title Test">
        </head>
        </html>
        '''
        metadata = extract_metadata_from_html(html)
        assert metadata["title"] == "Citation Title Test"

    def test_title_tag_extraction(self):
        """Test title extraction from <title> tag."""
        html = '''
        <html>
        <head>
            <title>Article Title | Nature Medicine</title>
        </head>
        </html>
        '''
        metadata = extract_metadata_from_html(html)
        assert metadata["title"] == "Article Title"

    def test_citation_publication_date(self):
        """Test date extraction from citation_publication_date."""
        html = '''
        <html>
        <head>
            <meta name="citation_publication_date" content="2024/03/15">
        </head>
        </html>
        '''
        metadata = extract_metadata_from_html(html)
        assert metadata["published_date"] == "2024-03-15T00:00:00"

    def test_citation_journal_title(self):
        """Test source extraction from citation_journal_title."""
        html = '''
        <html>
        <head>
            <meta name="citation_journal_title" content="Nature Medicine">
        </head>
        </html>
        '''
        metadata = extract_metadata_from_html(html)
        assert metadata["source"] == "Nature Medicine"

    def test_minimal_article_fixture(self):
        """Test metadata extraction from minimal article."""
        html_path = FIXTURES_DIR / "minimal_article.html"
        if not html_path.exists():
            pytest.skip("Minimal article fixture not found")

        html = html_path.read_text()
        metadata = extract_metadata_from_html(html)

        assert metadata["title"] == "A minimal article without rich metadata"
        assert metadata["published_date"] == "2024-01-10T00:00:00"


class TestParseDateString:
    """Tests for _parse_date_string function."""

    def test_iso8601_date(self):
        """Test parsing ISO8601 date."""
        assert _parse_date_string("2024-03-15") == "2024-03-15T00:00:00"

    def test_iso8601_datetime(self):
        """Test parsing ISO8601 datetime."""
        assert _parse_date_string("2024-03-15T10:30:00Z") == "2024-03-15T10:30:00Z"

    def test_slash_date(self):
        """Test parsing slash-separated date."""
        assert _parse_date_string("2024/03/15") == "2024-03-15T00:00:00"

    def test_month_dd_yyyy(self):
        """Test parsing 'Month DD, YYYY' format."""
        assert _parse_date_string("March 15, 2024") == "2024-03-15T00:00:00"

    def test_month_d_yyyy(self):
        """Test parsing 'Month D, YYYY' format."""
        assert _parse_date_string("January 5, 2024") == "2024-01-05T00:00:00"

    def test_dd_month_yyyy(self):
        """Test parsing 'DD Month YYYY' format."""
        assert _parse_date_string("15 March 2024") == "2024-03-15T00:00:00"

    def test_short_month_name(self):
        """Test parsing with abbreviated month name."""
        assert _parse_date_string("Jan 10, 2024") == "2024-01-10T00:00:00"

    def test_invalid_date(self):
        """Test that invalid date returns None."""
        assert _parse_date_string("invalid date") is None

    def test_empty_string(self):
        """Test that empty string returns None."""
        assert _parse_date_string("") is None

    def test_none_input(self):
        """Test that None input returns None."""
        assert _parse_date_string(None) is None


class TestExtractDoiFromJsonld:
    """Tests for _extract_doi_from_jsonld function."""

    def test_doi_field(self):
        """Test extracting DOI from 'doi' field."""
        data = {"doi": "10.1038/s41586-024-07051-0"}
        assert _extract_doi_from_jsonld(data) == "10.1038/s41586-024-07051-0"

    def test_identifier_field_with_url(self):
        """Test extracting DOI from 'identifier' field with URL."""
        data = {"identifier": "https://doi.org/10.1038/s41586-024-07051-0"}
        assert _extract_doi_from_jsonld(data) == "10.1038/s41586-024-07051-0"

    def test_property_value_identifier(self):
        """Test extracting DOI from PropertyValue identifier."""
        data = {
            "identifier": [{
                "@type": "PropertyValue",
                "propertyID": "doi",
                "value": "10.1038/s41586-024-07051-0"
            }]
        }
        assert _extract_doi_from_jsonld(data) == "10.1038/s41586-024-07051-0"

    def test_same_as_field(self):
        """Test extracting DOI from 'sameAs' field."""
        data = {"sameAs": "https://doi.org/10.1038/s41586-024-07051-0"}
        assert _extract_doi_from_jsonld(data) == "10.1038/s41586-024-07051-0"

    def test_no_doi(self):
        """Test that data without DOI returns None."""
        data = {"title": "Some Article", "author": "John Doe"}
        assert _extract_doi_from_jsonld(data) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
