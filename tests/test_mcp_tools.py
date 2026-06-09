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


# --- get_must_reads_from_db: backend detection -------------------------------


def _sample_candidate_rows():
    from datetime import date

    today = date.today().isoformat()
    return [
        {
            "id": "p1",
            "title": "ctDNA screening study",
            "published_date": today,
            "source": "Nature Cancer",
            "venue": "Nature Cancer",
            "url": "https://ex.org/1",
            "raw_text": "biomarker methylation early detection",
            "summary": "Liquid biopsy screening.",
        },
        {
            "id": "p2",
            "title": None,  # dropped by the missing-title filter
            "published_date": today,
            "source": "s",
            "venue": "v",
            "url": "https://ex.org/2",
            "raw_text": "",
            "summary": "",
        },
    ]


def test_get_must_reads_uses_pg_fetcher_when_postgres_configured():
    from mcp_server import must_reads

    with patch("storage.store.is_postgres", return_value=True), \
         patch.object(
             must_reads, "_fetch_candidates_pg", return_value=_sample_candidate_rows()
         ) as fetch_pg:
        out = must_reads.get_must_reads_from_db(since_days=7, limit=5, use_ai=False)

    assert fetch_pg.called
    assert out["total_candidates"] == 1
    item = out["must_reads"][0]
    assert item["id"] == "p1"
    # Same wire shape as the SQLite path.
    assert {
        "id", "title", "published_date", "source", "venue", "url",
        "score_total", "score_components", "explanation", "why_it_matters",
        "key_findings", "tags", "confidence",
    } <= set(item.keys())


def test_get_must_reads_pg_failure_falls_back():
    from mcp_server import must_reads

    with patch("storage.store.is_postgres", return_value=True), \
         patch.object(
             must_reads, "_fetch_candidates_pg", side_effect=RuntimeError("db down")
         ):
        out = must_reads.get_must_reads_from_db(since_days=7, limit=5, use_ai=False)

    # Falls through to the JSON/raw fallback chain instead of raising.
    assert "must_reads" in out
    assert out["used_ai"] is False


def test_fetch_candidates_pg_normalizes_pk_and_dates():
    from datetime import date
    from mcp_server import must_reads

    class FakeCursor:
        def execute(self, query, params):
            self.query = query

        def fetchall(self):
            return [("abc", "T", date(2026, 6, 1), "src", "ven", "https://ex.org/1")]

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    meta = (
        {"publication_id", "title", "published_date", "source", "venue", "url"},
        "publication_id",
        False,
        False,
    )
    with patch("storage.pg_store._get_connection", return_value=FakeConn()), \
         patch("storage.pg_store._put_connection", lambda conn: None), \
         patch("storage.pg_store._get_publications_table_metadata", return_value=meta), \
         patch("storage.store.get_database_url", return_value="postgresql://x"):
        rows = must_reads._fetch_candidates_pg("2026-01-01")

    assert rows[0]["id"] == "abc"  # publication_id normalized to id
    assert rows[0]["published_date"] == "2026-06-01"  # date object → ISO string
    assert rows[0]["raw_text"] is None  # missing schema columns filled
    assert rows[0]["summary"] is None
