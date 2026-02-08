"""Tests for canonical URL resolution logic."""

import pytest

from enrich.canonical_url import (
    normalize_url,
    extract_doi,
    extract_pmid,
    extract_arxiv_id,
    detect_source_type,
    resolve_canonical_url,
    build_doi_url,
    build_pubmed_url,
)


class TestNormalizeUrl:
    """Tests for URL normalization."""

    def test_normalize_https(self):
        """Test that HTTP is upgraded to HTTPS."""
        result = normalize_url("http://example.com/article")
        assert result.startswith("https://")

    def test_normalize_strips_whitespace(self):
        """Test that whitespace is stripped."""
        result = normalize_url("  https://example.com/article  ")
        assert result == "https://example.com/article"

    def test_normalize_removes_tracking_params(self):
        """Test that tracking params are removed."""
        url = "https://example.com/article?utm_source=twitter&id=123"
        result = normalize_url(url)
        assert "utm_source" not in result
        assert "id=123" in result

    def test_normalize_lowercase_host(self):
        """Test that host is lowercased."""
        result = normalize_url("https://Example.COM/Article")
        assert "example.com" in result

    def test_normalize_invalid_returns_none(self):
        """Test that invalid URLs return None."""
        assert normalize_url("") is None
        assert normalize_url("   ") is None
        assert normalize_url("not-a-url") is None

    def test_normalize_removes_fragment(self):
        """Test that URL fragments are removed."""
        result = normalize_url("https://example.com/article#section")
        assert "#" not in result


class TestExtractDoi:
    """Tests for DOI extraction."""

    def test_extract_doi_from_url(self):
        """Test DOI extraction from doi.org URL."""
        text = "https://doi.org/10.1038/s41586-021-03819-2"
        result = extract_doi(text)
        assert result == "10.1038/s41586-021-03819-2"

    def test_extract_doi_with_prefix(self):
        """Test DOI extraction with doi: prefix."""
        text = "DOI: 10.1038/s41586-021-03819-2"
        result = extract_doi(text)
        assert result == "10.1038/s41586-021-03819-2"

    def test_extract_doi_lowercase(self):
        """Test that extracted DOI is lowercased."""
        text = "https://doi.org/10.1038/S41586-021-03819-2"
        result = extract_doi(text)
        assert result == "10.1038/s41586-021-03819-2"

    def test_extract_doi_trims_punctuation(self):
        """Test that trailing punctuation is trimmed."""
        text = "See DOI: 10.1038/s41586-021-03819-2."
        result = extract_doi(text)
        assert result == "10.1038/s41586-021-03819-2"

    def test_extract_doi_none_when_missing(self):
        """Test that None is returned when no DOI found."""
        assert extract_doi("No DOI here") is None
        assert extract_doi("") is None
        assert extract_doi(None) is None

    def test_extract_doi_biorxiv(self):
        """Test DOI extraction from bioRxiv format."""
        text = "doi: 10.1101/2024.01.15.575123"
        result = extract_doi(text)
        assert result == "10.1101/2024.01.15.575123"


class TestExtractPmid:
    """Tests for PMID extraction."""

    def test_extract_pmid_from_url(self):
        """Test PMID extraction from PubMed URL."""
        text = "https://pubmed.ncbi.nlm.nih.gov/12345678/"
        result = extract_pmid(text)
        assert result == "12345678"

    def test_extract_pmid_with_prefix(self):
        """Test PMID extraction with PMID: prefix."""
        text = "PMID: 12345678"
        result = extract_pmid(text)
        assert result == "12345678"

    def test_extract_pmid_none_when_missing(self):
        """Test that None is returned when no PMID found."""
        assert extract_pmid("No PMID here") is None
        assert extract_pmid("") is None
        assert extract_pmid(None) is None


class TestExtractArxivId:
    """Tests for arXiv ID extraction."""

    def test_extract_arxiv_from_url(self):
        """Test arXiv ID extraction from URL."""
        text = "https://arxiv.org/abs/2401.12345"
        result = extract_arxiv_id(text)
        assert result == "2401.12345"

    def test_extract_arxiv_with_version(self):
        """Test arXiv ID extraction with version."""
        text = "arXiv:2401.12345v2"
        result = extract_arxiv_id(text)
        assert result == "2401.12345v2"

    def test_extract_arxiv_none_when_missing(self):
        """Test that None is returned when no arXiv ID found."""
        assert extract_arxiv_id("No arXiv ID here") is None


class TestDetectSourceType:
    """Tests for source type detection."""

    def test_detect_pubmed(self):
        """Test PubMed source detection."""
        assert detect_source_type("https://pubmed.ncbi.nlm.nih.gov/123/", "") == "pubmed"
        assert detect_source_type("", "PubMed Cancer") == "pubmed"

    def test_detect_biorxiv(self):
        """Test bioRxiv source detection."""
        assert detect_source_type("https://www.biorxiv.org/content/123", "") == "biorxiv"
        assert detect_source_type("", "bioRxiv") == "biorxiv"

    def test_detect_medrxiv(self):
        """Test medRxiv source detection."""
        assert detect_source_type("https://www.medrxiv.org/content/123", "") == "medrxiv"

    def test_detect_arxiv(self):
        """Test arXiv source detection."""
        assert detect_source_type("https://arxiv.org/abs/2401.123", "") == "arxiv"

    def test_detect_nature(self):
        """Test Nature source detection."""
        assert detect_source_type("https://www.nature.com/articles/123", "") == "nature"

    def test_detect_rss_fallback(self):
        """Test that unknown sources return 'rss'."""
        assert detect_source_type("https://unknown.com/article", "Unknown Source") == "rss"


class TestResolveCanonicalUrl:
    """Tests for canonical URL resolution."""

    def test_resolve_with_doi(self):
        """Test that DOI produces doi.org URL."""
        pub = {
            "id": "test123",
            "title": "Test Article",
            "url": "",
            "doi": "10.1038/s41586-021-03819-2",
            "pmid": None,
            "source": "Nature",
            "raw_text": "",
        }

        canonical_url, doi, pmid, source_type = resolve_canonical_url(pub)

        assert canonical_url == "https://doi.org/10.1038/s41586-021-03819-2"
        assert doi == "10.1038/s41586-021-03819-2"

    def test_resolve_with_pmid(self):
        """Test that PMID produces PubMed URL when no DOI."""
        pub = {
            "id": "test123",
            "title": "Test Article",
            "url": "",
            "doi": None,
            "pmid": "12345678",
            "source": "PubMed Cancer",
            "raw_text": "",
        }

        canonical_url, doi, pmid, source_type = resolve_canonical_url(pub)

        # URL is normalized (trailing slash may be stripped)
        assert "pubmed.ncbi.nlm.nih.gov/12345678" in canonical_url
        assert pmid == "12345678"

    def test_resolve_extracts_doi_from_url(self):
        """Test that DOI is extracted from URL."""
        pub = {
            "id": "test123",
            "title": "Test Article",
            "url": "https://doi.org/10.1038/s41586-021-03819-2",
            "doi": None,
            "pmid": None,
            "source": "Nature",
            "raw_text": "",
        }

        canonical_url, doi, pmid, source_type = resolve_canonical_url(pub)

        assert doi == "10.1038/s41586-021-03819-2"
        assert canonical_url == "https://doi.org/10.1038/s41586-021-03819-2"

    def test_resolve_extracts_pmid_from_pubmed_url(self):
        """Test that PMID is extracted from PubMed URL."""
        pub = {
            "id": "test123",
            "title": "Test Article",
            "url": "https://pubmed.ncbi.nlm.nih.gov/12345678/",
            "doi": None,
            "pmid": None,
            "source": "PubMed",
            "raw_text": "",
        }

        canonical_url, doi, pmid, source_type = resolve_canonical_url(pub)

        assert pmid == "12345678"
        assert "pubmed.ncbi.nlm.nih.gov" in canonical_url

    def test_resolve_normalizes_existing_url(self):
        """Test that existing URL is normalized when no identifiers."""
        pub = {
            "id": "test123",
            "title": "Test Article",
            "url": "http://example.com/article?utm_source=test",
            "doi": None,
            "pmid": None,
            "source": "Unknown",
            "raw_text": "",
        }

        canonical_url, doi, pmid, source_type = resolve_canonical_url(pub)

        assert canonical_url.startswith("https://")
        assert "utm_source" not in canonical_url

    def test_resolve_returns_none_when_unresolvable(self):
        """Test that None is returned when no URL can be resolved."""
        pub = {
            "id": "test123",
            "title": "Test Article",
            "url": "",
            "doi": None,
            "pmid": None,
            "source": "Unknown",
            "raw_text": "",
        }

        canonical_url, doi, pmid, source_type = resolve_canonical_url(pub)

        assert canonical_url is None


class TestBuildUrls:
    """Tests for URL building functions."""

    def test_build_doi_url(self):
        """Test DOI URL building."""
        assert build_doi_url("10.1038/s41586") == "https://doi.org/10.1038/s41586"

    def test_build_doi_url_strips_prefix(self):
        """Test that existing prefix is stripped."""
        assert build_doi_url("https://doi.org/10.1038/s41586") == "https://doi.org/10.1038/s41586"
        assert build_doi_url("doi:10.1038/s41586") == "https://doi.org/10.1038/s41586"

    def test_build_pubmed_url(self):
        """Test PubMed URL building."""
        assert build_pubmed_url("12345678") == "https://pubmed.ncbi.nlm.nih.gov/12345678/"
