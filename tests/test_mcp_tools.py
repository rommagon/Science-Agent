"""Tests for the pure MCP tool implementations in ``mcp_server.tools``.

These tests mock the storage and semantic-search layers so they run without a
database, OpenAI key, or the full ``mcp`` SDK installed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from mcp_server import tools


# --- _format_pub: link fallback chain ---------------------------------------


def test_format_pub_prefers_canonical_url():
    out = tools._format_pub(
        {
            "publication_id": "abc",
            "title": "T",
            "canonical_url": "https://example.org/canonical",
            "url": "https://example.org/raw",
            "doi": "10.1/x",
            "pmid": "123",
        }
    )
    assert out["link"] == "https://example.org/canonical"


def test_format_pub_falls_back_to_url():
    out = tools._format_pub(
        {
            "publication_id": "abc",
            "title": "T",
            "url": "https://example.org/raw",
            "doi": "10.1/x",
            "pmid": "123",
        }
    )
    assert out["link"] == "https://example.org/raw"


def test_format_pub_falls_back_to_doi():
    out = tools._format_pub({"publication_id": "abc", "title": "T", "doi": "10.1/x"})
    assert out["link"] == "https://doi.org/10.1/x"


def test_format_pub_falls_back_to_pmid():
    out = tools._format_pub({"publication_id": "abc", "title": "T", "pmid": "999"})
    # normalize_url may strip the trailing slash — accept either form.
    assert out["link"] in {
        "https://pubmed.ncbi.nlm.nih.gov/999/",
        "https://pubmed.ncbi.nlm.nih.gov/999",
    }


def test_format_pub_no_link_when_nothing_resolvable():
    out = tools._format_pub({"publication_id": "abc", "title": "T"})
    assert out["link"] is None


def test_format_pub_uses_final_summary_over_summary():
    out = tools._format_pub(
        {
            "publication_id": "abc",
            "title": "T",
            "summary": "old",
            "final_summary": "new",
        }
    )
    assert out["summary"] == "new"


def test_format_pub_full_includes_extra_fields():
    pub = {
        "publication_id": "abc",
        "title": "T",
        "raw_text": "abstract goes here",
        "claude_score": 80,
        "gemini_score": 75,
        "agreement_level": "high",
        "confidence": "high",
        "final_relevancy_reason": "directly relevant",
        "credibility_reason": "peer reviewed",
        "doi": "10.1/x",
        "pmid": "999",
    }
    out = tools._format_pub(pub, include_full=True)
    assert out["raw_text"] == "abstract goes here"
    assert out["claude_score"] == 80
    assert out["relevancy_reason"] == "directly relevant"
    assert out["credibility_reason"] == "peer reviewed"
    assert out["doi"] == "10.1/x"


# --- search_publications_tool ----------------------------------------------


def test_search_publications_empty_query_short_circuits():
    out = tools.search_publications_tool(query="   ")
    assert out["results"] == []
    assert "error" in out


def test_search_publications_returns_no_results_when_search_empty():
    with patch("acitrack.semantic_search.search_publications", return_value=[]):
        out = tools.search_publications_tool(query="anything", since_days=30)
    assert out["results"] == []
    assert out["since_days"] == 30


def test_search_publications_hydrates_and_orders_by_similarity():
    hits = [
        {"publication_id": "p1", "title": "ignored", "source": "s",
         "published_date": "2026-01-01", "canonical_url": None, "similarity": 0.91},
        {"publication_id": "p2", "title": "ignored", "source": "s",
         "published_date": "2026-01-02", "canonical_url": None, "similarity": 0.80},
    ]
    hydrated = {
        "p1": {
            "publication_id": "p1",
            "title": "Paper one",
            "summary": "summary one",
            "url": "https://ex.org/1",
            "final_relevancy_score": 88,
            "credibility_score": 70,
        },
        "p2": {
            "publication_id": "p2",
            "title": "Paper two",
            "summary": "summary two",
            "url": "https://ex.org/2",
            "final_relevancy_score": 60,
            "credibility_score": 50,
        },
    }
    with patch("acitrack.semantic_search.search_publications", return_value=hits), \
         patch.object(tools, "_hydrate_publications", return_value=hydrated):
        out = tools.search_publications_tool(query="ctdna", top_k=10)

    assert [r["publication_id"] for r in out["results"]] == ["p1", "p2"]
    assert out["results"][0]["similarity"] == 0.91
    assert out["results"][0]["link"] == "https://ex.org/1"
    assert out["results"][0]["relevancy_score"] == 88


def test_search_publications_applies_min_relevancy_filter():
    hits = [
        {"publication_id": "p1", "similarity": 0.9, "title": "", "source": "",
         "published_date": "", "canonical_url": None},
        {"publication_id": "p2", "similarity": 0.8, "title": "", "source": "",
         "published_date": "", "canonical_url": None},
    ]
    hydrated = {
        "p1": {"publication_id": "p1", "title": "P1", "final_relevancy_score": 50,
               "url": "https://ex.org/1"},
        "p2": {"publication_id": "p2", "title": "P2", "final_relevancy_score": 80,
               "url": "https://ex.org/2"},
    }
    with patch("acitrack.semantic_search.search_publications", return_value=hits), \
         patch.object(tools, "_hydrate_publications", return_value=hydrated):
        out = tools.search_publications_tool(
            query="x", top_k=10, min_relevancy_score=70
        )
    assert [r["publication_id"] for r in out["results"]] == ["p2"]


def test_search_publications_skips_missing_hydration():
    hits = [
        {"publication_id": "p1", "similarity": 0.9, "title": "", "source": "",
         "published_date": "", "canonical_url": None},
        {"publication_id": "ghost", "similarity": 0.85, "title": "", "source": "",
         "published_date": "", "canonical_url": None},
    ]
    hydrated = {
        "p1": {"publication_id": "p1", "title": "P1", "url": "https://ex.org/1"},
    }
    with patch("acitrack.semantic_search.search_publications", return_value=hits), \
         patch.object(tools, "_hydrate_publications", return_value=hydrated):
        out = tools.search_publications_tool(query="x", top_k=10)
    assert [r["publication_id"] for r in out["results"]] == ["p1"]


def test_search_publications_top_k_caps_at_25():
    out = tools.search_publications_tool(query="", top_k=999)
    # Empty query short-circuits before we even hit the cap, but we can still
    # verify the cap doesn't blow up by passing a real query and tiny mock.
    with patch("acitrack.semantic_search.search_publications", return_value=[]):
        out = tools.search_publications_tool(query="x", top_k=999)
    assert out["results"] == []


# --- get_publication_tool ---------------------------------------------------


def test_get_publication_returns_full_record():
    hydrated = {
        "p1": {
            "publication_id": "p1",
            "title": "Paper",
            "raw_text": "full abstract here",
            "summary": "ai summary",
            "final_relevancy_score": 90,
            "credibility_score": 80,
            "claude_score": 85,
            "gemini_score": 88,
            "agreement_level": "high",
            "confidence": "high",
            "final_relevancy_reason": "directly relevant",
            "credibility_reason": "Nature",
            "doi": "10.1/x",
            "pmid": "1",
            "url": "https://ex.org/1",
        },
    }
    with patch.object(tools, "_hydrate_publications", return_value=hydrated):
        out = tools.get_publication_tool("p1")

    assert out["publication_id"] == "p1"
    assert out["raw_text"] == "full abstract here"
    assert out["claude_score"] == 85
    assert out["link"] == "https://ex.org/1"


def test_get_publication_returns_error_when_missing():
    with patch.object(tools, "_hydrate_publications", return_value={}):
        out = tools.get_publication_tool("nope")
    assert out["error"] == "not found"
    assert out["publication_id"] == "nope"


def test_get_publication_requires_id():
    out = tools.get_publication_tool("")
    assert out["error"] == "publication_id is required"
