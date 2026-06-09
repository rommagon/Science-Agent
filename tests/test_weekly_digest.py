"""Tests for weekly digest generation."""

import os
import sqlite3
import tempfile
from datetime import date

import pytest

# Add parent directory to path for imports
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from digest.data_access import (
    _build_link,
    _get_publications_sqlite,
    _process_publications,
    _extract_key_findings,
    _clean_why_it_matters,
    _generate_fallback_why_it_matters,
    log_publication_feedback,
    score_to_ordinal,
)
from digest.feedback import build_feedback_url, verify_feedback_signature


class TestBuildLink:
    """Tests for link building fallback logic."""

    def test_canonical_url_priority(self):
        """Test that canonical_url is used first."""
        pub = {
            "canonical_url": "https://canonical.example.com",
            "url": "https://normal.example.com",
            "doi": "10.1234/test",
            "pmid": "12345678",
        }
        assert _build_link(pub) == "https://canonical.example.com"

    def test_url_fallback(self):
        """Test that url is used when canonical_url is missing."""
        pub = {
            "canonical_url": None,
            "url": "https://normal.example.com",
            "doi": "10.1234/test",
            "pmid": "12345678",
        }
        assert _build_link(pub) == "https://normal.example.com"

    def test_doi_fallback(self):
        """Test that DOI is used when url is missing."""
        pub = {
            "canonical_url": None,
            "url": None,
            "doi": "10.1234/test",
            "pmid": "12345678",
        }
        assert _build_link(pub) == "https://doi.org/10.1234/test"

    def test_pmid_fallback(self):
        """Test that PMID is used when DOI is missing."""
        pub = {
            "canonical_url": None,
            "url": None,
            "doi": None,
            "pmid": "12345678",
        }
        # normalize_url may strip the trailing slash — accept either form.
        assert _build_link(pub) in {
            "https://pubmed.ncbi.nlm.nih.gov/12345678/",
            "https://pubmed.ncbi.nlm.nih.gov/12345678",
        }

    def test_no_link_available(self):
        """Test that None is returned when no identifiers present."""
        pub = {
            "canonical_url": None,
            "url": None,
            "doi": None,
            "pmid": None,
        }
        assert _build_link(pub) is None

    def test_empty_strings_treated_as_missing(self):
        """Test that empty strings are treated as missing."""
        pub = {
            "canonical_url": "",
            "url": "",
            "doi": "10.1234/test",
            "pmid": None,
        }
        assert _build_link(pub) == "https://doi.org/10.1234/test"

    def test_unsafe_scheme_falls_through_to_next_candidate(self):
        """javascript:/data: URLs never reach hrefs; safe fallback is used."""
        pub = {
            "canonical_url": "javascript:alert(1)",
            "url": "data:text/html,<script>alert(1)</script>",
            "doi": "10.1234/test",
            "pmid": None,
        }
        assert _build_link(pub) == "https://doi.org/10.1234/test"

    def test_unsafe_scheme_with_no_fallback_returns_none(self):
        """When the only candidate has a weird scheme, return None."""
        pub = {
            "canonical_url": "javascript:alert(1)",
            "url": None,
            "doi": None,
            "pmid": None,
        }
        assert _build_link(pub) is None


class TestProcessPublications:
    """Tests for publication processing and selection."""

    def test_selection_by_score(self):
        """Test that highest scoring publications are selected."""
        pubs = [
            {"id": "pub1", "title": "Low Score", "published_date": "2026-01-20", "source": "A", "relevancy_score": 50},
            {"id": "pub2", "title": "High Score", "published_date": "2026-01-20", "source": "B", "relevancy_score": 90},
            {"id": "pub3", "title": "Mid Score", "published_date": "2026-01-20", "source": "C", "relevancy_score": 70},
        ]

        result = _process_publications(
            pubs,
            week_start=date(2026, 1, 19),
            week_end=date(2026, 1, 25),
            top_n=2,
            honorable_mentions=0,
            must_reads_data={},
            tri_model_data={},
        )

        assert len(result["must_reads"]) == 2
        assert result["must_reads"][0]["title"] == "High Score"
        assert result["must_reads"][1]["title"] == "Mid Score"
        assert result["scoring_method"] == "relevancy_only"

    def test_deterministic_tiebreak(self):
        """Test that ties are broken deterministically (date desc, title asc)."""
        pubs = [
            {"id": "pub1", "title": "Zebra Study", "published_date": "2026-01-20", "source": "A", "relevancy_score": 80},
            {"id": "pub2", "title": "Alpha Study", "published_date": "2026-01-20", "source": "B", "relevancy_score": 80},
            {"id": "pub3", "title": "Beta Study", "published_date": "2026-01-21", "source": "C", "relevancy_score": 80},
        ]

        result = _process_publications(
            pubs,
            week_start=date(2026, 1, 19),
            week_end=date(2026, 1, 25),
            top_n=3,
            honorable_mentions=0,
            must_reads_data={},
            tri_model_data={},
        )

        titles = [p["title"] for p in result["must_reads"]]
        # pub3 is newer (Jan 21) so comes first
        # pub1 and pub2 have same date, alphabetically Alpha < Zebra
        assert titles == ["Beta Study", "Alpha Study", "Zebra Study"]

    def test_honorable_mentions_after_top_n(self):
        """Test that honorable mentions come after top N."""
        pubs = [
            {"id": "pub1", "title": "First", "published_date": "2026-01-20", "source": "A", "relevancy_score": 90},
            {"id": "pub2", "title": "Second", "published_date": "2026-01-20", "source": "B", "relevancy_score": 80},
            {"id": "pub3", "title": "Third", "published_date": "2026-01-20", "source": "C", "relevancy_score": 70},
            {"id": "pub4", "title": "Fourth", "published_date": "2026-01-20", "source": "D", "relevancy_score": 60},
        ]

        result = _process_publications(
            pubs,
            week_start=date(2026, 1, 19),
            week_end=date(2026, 1, 25),
            top_n=2,
            honorable_mentions=2,
            must_reads_data={},
            tri_model_data={},
        )

        assert len(result["must_reads"]) == 2
        assert len(result["honorable_mentions"]) == 2
        assert result["must_reads"][0]["title"] == "First"
        assert result["must_reads"][1]["title"] == "Second"
        assert result["honorable_mentions"][0]["title"] == "Third"
        assert result["honorable_mentions"][1]["title"] == "Fourth"

    def test_relevancy_overrides_credibility(self):
        """Test that relevancy strictly outranks credibility for ordering."""
        pubs = [
            {
                "id": "pub1",
                "title": "High Credibility Low Relevance",
                "published_date": "2026-01-20",
                "source": "A",
                "relevancy_score": 70,
                "credibility_score": 95,
            },
            {
                "id": "pub2",
                "title": "High Relevance Low Credibility",
                "published_date": "2026-01-19",
                "source": "B",
                "relevancy_score": 90,
                "credibility_score": 40,
            },
        ]

        result = _process_publications(
            pubs,
            week_start=date(2026, 1, 19),
            week_end=date(2026, 1, 25),
            top_n=2,
            honorable_mentions=0,
            must_reads_data={},
            tri_model_data={},
        )

        assert result["must_reads"][0]["title"] == "High Relevance Low Credibility"

    def test_total_candidates_count(self):
        """Test that total_candidates counts scored publications only.

        total_candidates intentionally excludes publications without a
        relevancy_score, so unscored rows do not inflate the "we reviewed
        N publications" claim in the digest copy.
        """
        pubs = [
            {"id": f"pub{i}", "title": f"Pub {i}", "published_date": "2026-01-20", "source": "A", "relevancy_score": 60 + i}
            for i in range(10)
        ]
        # Unscored publications must not be counted
        pubs += [
            {"id": f"unscored{i}", "title": f"Unscored {i}", "published_date": "2026-01-20", "source": "A"}
            for i in range(3)
        ]

        result = _process_publications(
            pubs,
            week_start=date(2026, 1, 19),
            week_end=date(2026, 1, 25),
            top_n=3,
            honorable_mentions=0,
            must_reads_data={},
            tri_model_data={},
        )

        assert result["total_candidates"] == 10


class TestSqliteDateFilter:
    """Tests for the sargable ISO-string date filter in the SQLite path."""

    def _make_db(self, db_path, rows):
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("""
                CREATE TABLE publications (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    published_date TEXT,
                    source TEXT,
                    final_relevancy_score REAL
                )
            """)
            conn.executemany(
                "INSERT INTO publications VALUES (?, ?, ?, ?, ?)", rows
            )
            conn.commit()
        finally:
            conn.close()

    def test_week_range_inclusive_with_and_without_time_component(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "pubs.db")
            self._make_db(db_path, [
                ("p1", "Start Boundary", "2026-01-19", "A", 90),
                ("p2", "Mid Week", "2026-01-22", "A", 85),
                ("p3", "End Day With Time", "2026-01-25T18:30:00", "A", 80),
                ("p4", "Before Range", "2026-01-18", "A", 95),
                ("p5", "After Range", "2026-01-26", "A", 95),
            ])

            result = _get_publications_sqlite(
                week_start=date(2026, 1, 19),
                week_end=date(2026, 1, 25),
                top_n=5,
                honorable_mentions=0,
                db_path=db_path,
            )

            titles = {p["title"] for p in result["must_reads"]}
            assert titles == {"Start Boundary", "Mid Week", "End Day With Time"}


class TestExtractKeyFindings:
    """Tests for key findings extraction."""

    def test_extract_from_list(self):
        """Test extracting findings from a list."""
        must_read = {
            "key_findings": ["Finding 1", "Finding 2", "Finding 3", "Finding 4"]
        }
        findings = _extract_key_findings(must_read, {}, {})
        assert findings == ["Finding 1", "Finding 2", "Finding 3"]

    def test_extract_from_bullet_summary(self):
        """Test extracting findings from bullet-point summary."""
        must_read = {
            "final_summary": "Overview • First point • Second point • Third point"
        }
        findings = _extract_key_findings(must_read, {}, {})
        assert len(findings) <= 3
        assert any("point" in f.lower() for f in findings)

    def test_extract_from_sentence_summary(self):
        """Test extracting findings from sentence-based summary."""
        must_read = {
            "final_summary": "First sentence about study. Second sentence about results. Third sentence about implications."
        }
        findings = _extract_key_findings(must_read, {}, {})
        assert len(findings) <= 3


class TestScoreToOrdinal:
    """Tests for score to ordinal label conversion."""

    def test_high_score(self):
        """Test that 80+ scores are 'High'."""
        assert score_to_ordinal(80) == "High"
        assert score_to_ordinal(95) == "High"
        assert score_to_ordinal(100) == "High"

    def test_moderate_score(self):
        """Test that 65-79 scores are 'Moderate'."""
        assert score_to_ordinal(65) == "Moderate"
        assert score_to_ordinal(70) == "Moderate"
        assert score_to_ordinal(79) == "Moderate"

    def test_exploratory_score(self):
        """Test that <65 scores are 'Exploratory'."""
        assert score_to_ordinal(64) == "Exploratory"
        assert score_to_ordinal(50) == "Exploratory"
        assert score_to_ordinal(0) == "Exploratory"

    def test_none_score(self):
        """Test that None scores are 'Exploratory'."""
        assert score_to_ordinal(None) == "Exploratory"


class TestCleanWhyItMatters:
    """Tests for reviewer attribution removal."""

    def test_removes_claude_attribution(self):
        """Test removal of Claude attribution."""
        text = "Claude's review found that this study presents significant findings."
        cleaned = _clean_why_it_matters(text)
        assert "Claude" not in cleaned
        assert "This study presents significant findings" in cleaned

    def test_removes_both_reviews_attribution(self):
        """Test removal of 'Both reviews agree' pattern."""
        text = "Both reviews agree that the methodology is sound."
        cleaned = _clean_why_it_matters(text)
        assert "Both reviews" not in cleaned
        assert "methodology is sound" in cleaned.lower()

    def test_removes_gemini_attribution(self):
        """Test removal of Gemini attribution."""
        text = "Gemini's analysis indicates strong clinical potential."
        cleaned = _clean_why_it_matters(text)
        assert "Gemini" not in cleaned
        assert "clinical potential" in cleaned.lower()

    def test_preserves_clean_text(self):
        """Test that clean text is preserved."""
        text = "This study demonstrates a novel biomarker approach."
        cleaned = _clean_why_it_matters(text)
        assert cleaned == text

    def test_capitalizes_first_letter(self):
        """Test that first letter is capitalized after cleaning."""
        text = "The Claude review found that this works well."
        cleaned = _clean_why_it_matters(text)
        assert cleaned[0].isupper()

    def test_truncates_to_two_sentences(self):
        """Test truncation to ~2 sentences."""
        text = "First sentence. Second sentence. Third sentence. Fourth sentence."
        cleaned = _clean_why_it_matters(text)
        # Should have at most 2 sentences
        sentences = [s for s in cleaned.split(".") if s.strip()]
        assert len(sentences) <= 2


class TestGenerateFallbackWhyItMatters:
    """Tests for fallback 'why it matters' generation."""

    def test_cancer_type_detection(self):
        """Test fallback with cancer type in title."""
        pub = {"title": "Lung cancer screening advances", "source": "Nature"}
        fallback = _generate_fallback_why_it_matters(pub)
        assert "lung" in fallback.lower()
        assert len(fallback) > 20

    def test_detection_method_in_title(self):
        """Test fallback with detection method in title."""
        pub = {"title": "Novel biomarker discovery for tumors", "source": "Science"}
        fallback = _generate_fallback_why_it_matters(pub)
        assert "biomarker" in fallback.lower()

    def test_venue_fallback(self):
        """Test fallback using venue when no keywords found."""
        pub = {"title": "Some generic study", "source": "Unknown", "venue": "Nature Medicine"}
        fallback = _generate_fallback_why_it_matters(pub)
        assert "Nature Medicine" in fallback

    def test_generic_fallback(self):
        """Test generic fallback when no info available."""
        pub = {"title": "Study", "source": ""}
        fallback = _generate_fallback_why_it_matters(pub)
        assert "early detection" in fallback.lower()


class TestDebugRanking:
    """Tests for debug ranking functionality."""

    def test_debug_ranking_returns_top_20(self):
        """Test that debug_ranking returns top 20 candidates."""
        pubs = [{
            "id": f"pub{i}",
            "title": f"Publication {i}",
            "published_date": "2026-01-20",
            "source": "Test",
            "relevancy_score": 90 - i,
        } for i in range(25)]

        result = _process_publications(
            pubs,
            week_start=date(2026, 1, 19),
            week_end=date(2026, 1, 25),
            top_n=5,
            honorable_mentions=0,
            must_reads_data={},
            tri_model_data={},
            debug_ranking=True,
        )

        assert "debug_ranking" in result
        assert len(result["debug_ranking"]["top_20_candidates"]) == 20

    def test_debug_ranking_includes_warnings(self):
        """Test that debug_ranking includes ranking warnings key."""
        pubs = [{
            "id": "pub1",
            "title": "Test",
            "published_date": "2026-01-20",
            "source": "A",
            "relevancy_score": 80,
        }]

        result = _process_publications(
            pubs,
            week_start=date(2026, 1, 19),
            week_end=date(2026, 1, 25),
            top_n=5,
            honorable_mentions=0,
            must_reads_data={},
            tri_model_data={},
            debug_ranking=True,
        )

        assert "ranking_warnings" in result["debug_ranking"]
        assert result["debug_ranking"]["ranking_method"] == "relevancy_only"

    def test_debug_ranking_score_counts(self):
        """Test that debug_ranking includes score distribution keys."""
        pubs = [
            {"id": "pub1", "title": "High", "published_date": "2026-01-20", "source": "A", "relevancy_score": 85},
            {"id": "pub2", "title": "Moderate", "published_date": "2026-01-20", "source": "B", "relevancy_score": 70},
            {"id": "pub3", "title": "Low", "published_date": "2026-01-20", "source": "C", "relevancy_score": 50},
        ]

        result = _process_publications(
            pubs,
            week_start=date(2026, 1, 19),
            week_end=date(2026, 1, 25),
            top_n=5,
            honorable_mentions=0,
            must_reads_data={},
            tri_model_data={},
            debug_ranking=True,
        )

        debug = result["debug_ranking"]
        assert "total_candidates" in debug
        assert "total_with_relevancy" in debug
        assert "relevancy_distribution" in debug

        # Ensure composite/final score fields are not present
        top_item = debug["top_20_candidates"][0]
        assert "final_score" not in top_item
        assert "composite_score" not in top_item
        assert "recency_boost" not in top_item


class TestStrictOrdering:
    """Tests for strict ranking order enforcement."""

    def test_higher_scores_always_ranked_first(self):
        """Test that higher-scored items always rank above lower-scored."""
        pubs = [
            {"id": "pub1", "title": "Low Score", "published_date": "2026-01-25", "source": "A", "relevancy_score": 50},
            {"id": "pub2", "title": "High Score", "published_date": "2026-01-19", "source": "B", "relevancy_score": 90},
        ]

        result = _process_publications(
            pubs,
            week_start=date(2026, 1, 19),
            week_end=date(2026, 1, 25),
            top_n=5,
            honorable_mentions=0,
            must_reads_data={},
            tri_model_data={},
        )

        # Even though pub1 has newer date, pub2 should rank first due to higher score
        assert result["must_reads"][0]["title"] == "High Score"

    def test_date_tiebreak_for_equal_scores(self):
        """Test that newer dates rank first when scores are equal."""
        pubs = [
            {"id": "pub1", "title": "Alpha", "published_date": "2026-01-19", "source": "A", "relevancy_score": 80},
            {"id": "pub2", "title": "Beta", "published_date": "2026-01-25", "source": "B", "relevancy_score": 80},
        ]

        result = _process_publications(
            pubs,
            week_start=date(2026, 1, 19),
            week_end=date(2026, 1, 25),
            top_n=5,
            honorable_mentions=0,
            must_reads_data={},
            tri_model_data={},
        )

        # Beta should rank first due to newer date (same score)
        assert result["must_reads"][0]["title"] == "Beta"

    def test_title_tiebreak_for_equal_scores_and_dates(self):
        """Test alphabetical title ordering for equal scores and dates."""
        pubs = [
            {"id": "pub1", "title": "Zebra Study", "published_date": "2026-01-20", "source": "A", "relevancy_score": 80},
            {"id": "pub2", "title": "Alpha Study", "published_date": "2026-01-20", "source": "B", "relevancy_score": 80},
        ]

        result = _process_publications(
            pubs,
            week_start=date(2026, 1, 19),
            week_end=date(2026, 1, 25),
            top_n=5,
            honorable_mentions=0,
            must_reads_data={},
            tri_model_data={},
        )

        # Alpha should rank first due to alphabetical ordering (same score and date)
        assert result["must_reads"][0]["title"] == "Alpha Study"


class TestOrdinalLabelsInOutput:
    """Tests for ordinal labels in processed publications."""

    def test_ordinal_labels_present(self):
        """Test that ordinal labels are added to publications."""
        pubs = [{
            "id": "pub1",
            "title": "Test",
            "published_date": "2026-01-20",
            "source": "A",
            "relevancy_score": 85,
            "credibility_score": 70,
        }]

        result = _process_publications(
            pubs,
            week_start=date(2026, 1, 19),
            week_end=date(2026, 1, 25),
            top_n=5,
            honorable_mentions=0,
            must_reads_data={},
            tri_model_data={},
        )

        assert result["must_reads"][0]["relevancy_ordinal"] == "High"
        assert result["must_reads"][0]["credibility_ordinal"] == "Moderate"


class TestTemplateRendering:
    """Tests for template rendering."""

    def test_html_template_renders(self):
        """Test that HTML template renders without exceptions."""
        from jinja2 import Environment, FileSystemLoader

        templates_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates"
        )
        env = Environment(loader=FileSystemLoader(templates_dir), autoescape=True)
        template = env.get_template("weekly_digest.html.j2")

        data = {
            "week_start": "2026-01-19",
            "week_end": "2026-01-25",
            "total_candidates": 100,
            "scoring_method": "relevancy_only",
            "must_reads": [
                {
                    "title": "Test Publication Title",
                    "source": "Nature",
                    "venue": "Nature Medicine",
                    "published_date": "2026-01-20",
                    "link": "https://example.com/article",
                    "relevancy_score": 85,
                    "credibility_score": 70,
                    "relevancy_ordinal": "High",
                    "credibility_ordinal": "Moderate",
                    "why_it_matters": "This is why it matters.",
                    "summary": "This is the summary.",
                    "key_findings": ["Finding 1", "Finding 2"],
                    "thumbs_up_url": "https://feedback.example.com/feedback?p=pub1&v=up",
                    "thumbs_down_url": "https://feedback.example.com/feedback?p=pub1&v=down",
                    "commercial_signals": {
                        "peer_reviewed": True,
                        "study_type": "clinical trial",
                        "human_cohort": True,
                    },
                },
            ],
            "honorable_mentions": [],
            "feedback_enabled": True,
        }

        html = template.render(**data)

        # Check that expected content is present
        assert "Research Digest" in html
        assert "Test Publication Title" in html
        assert "Nature" in html
        assert "85/100 Relevancy" in html
        assert "This is why it matters" in html
        assert "&#x1F44D;" in html

    def test_text_template_renders(self):
        """Test that text template renders without exceptions."""
        from jinja2 import Environment, FileSystemLoader

        templates_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates"
        )
        env = Environment(loader=FileSystemLoader(templates_dir), autoescape=True)
        template = env.get_template("weekly_digest.txt.j2")

        data = {
            "week_start": "2026-01-19",
            "week_end": "2026-01-25",
            "total_candidates": 100,
            "scoring_method": "relevancy_only",
            "must_reads": [
                {
                    "title": "Test Publication Title",
                    "source": "Nature",
                    "venue": "Nature Medicine",
                    "published_date": "2026-01-20",
                    "link": "https://example.com/article",
                    "relevancy_score": 85,
                    "credibility_score": 70,
                    "why_it_matters": "This is why it matters.",
                    "summary": "This is the summary.",
                    "key_findings": ["Finding 1", "Finding 2"],
                    "thumbs_up_url": "https://feedback.example.com/feedback?p=pub1&v=up",
                    "thumbs_down_url": "https://feedback.example.com/feedback?p=pub1&v=down",
                    "commercial_signals": {},
                },
            ],
            "honorable_mentions": [],
            "feedback_enabled": True,
        }

        text = template.render(**data)

        # Check that expected content is present
        assert "SPOTITEARLY MUST-READS" in text
        assert "Test Publication Title" in text
        assert "Nature" in text
        assert "Feedback: 👍" in text

    def test_template_handles_missing_optional_fields(self):
        """Test that templates handle missing optional fields gracefully."""
        from jinja2 import Environment, FileSystemLoader

        templates_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates"
        )
        env = Environment(loader=FileSystemLoader(templates_dir), autoescape=True)
        template = env.get_template("weekly_digest.html.j2")

        # Minimal data with many fields missing
        data = {
            "week_start": "2026-01-19",
            "week_end": "2026-01-25",
            "total_candidates": 50,
            "scoring_method": "relevancy_only",
            "must_reads": [
                {
                    "title": "Minimal Publication",
                    "source": "Unknown Source",
                    "published_date": None,
                    "link": None,
                    "relevancy_score": None,
                    "credibility_score": None,
                    "why_it_matters": None,
                    "summary": None,
                    "key_findings": None,
                    "commercial_signals": None,
                },
            ],
            "honorable_mentions": [],
            "feedback_enabled": False,
        }

        # Should not raise an exception
        html = template.render(**data)
        assert "Minimal Publication" in html


class TestSenders:
    """Tests for email senders."""

    def test_demo_sender_returns_success(self):
        """Test that demo sender returns success."""
        from digest.senders import DemoSender

        sender = DemoSender()
        result = sender.send(
            to=["test@example.com"],
            subject="Test Subject",
            html_content="<p>HTML</p>",
            text_content="Text",
        )

        assert result["success"] is True
        assert "demo" in result["message"].lower()

    def test_validate_sendgrid_config_detects_missing_keys(self):
        """Test that validation detects missing SendGrid config."""
        from digest.senders import validate_sendgrid_config

        # Save current env vars
        old_api_key = os.environ.pop("SENDGRID_API_KEY", None)
        old_from = os.environ.pop("FROM_EMAIL", None)

        try:
            result = validate_sendgrid_config()
            assert result["valid"] is False
            assert len(result["errors"]) >= 1
        finally:
            # Restore env vars
            if old_api_key:
                os.environ["SENDGRID_API_KEY"] = old_api_key
            if old_from:
                os.environ["FROM_EMAIL"] = old_from


class TestFeedbackLinks:
    """Tests for feedback URL signing and validation."""

    def test_build_feedback_url_has_verifiable_signature(self):
        secret = "test-secret"
        url = build_feedback_url(
            base_url="https://feedback.example.com/feedback",
            publication_id="pub-123",
            week_start="2026-01-19",
            week_end="2026-01-25",
            vote="up",
            secret=secret,
            ts=1234567890,
        )

        # parse manually to avoid adding urllib import in test scope
        query = url.split("?", 1)[1]
        params = {}
        for pair in query.split("&"):
            key, value = pair.split("=", 1)
            params[key] = value

        signed = {k: params[k] for k in ["p", "w", "e", "v", "t"]}
        assert verify_feedback_signature(signed, params["s"], secret)

    def test_build_feedback_url_rejects_invalid_vote(self):
        with pytest.raises(ValueError):
            build_feedback_url(
                base_url="https://feedback.example.com/feedback",
                publication_id="pub-123",
                week_start="2026-01-19",
                week_end="2026-01-25",
                vote="maybe",
                secret="test-secret",
                ts=1234567890,
            )


class TestFeedbackPersistence:
    """Tests for feedback storage."""

    def test_log_publication_feedback_sqlite(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "feedback.db")
            ok = log_publication_feedback(
                week_start="2026-01-19",
                week_end="2026-01-25",
                publication_id="pub-abc",
                vote="down",
                source_ip="127.0.0.1",
                user_agent="pytest-agent",
                context={"timestamp": 123},
                db_path=db_path,
            )
            assert ok is True

            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT publication_id, vote, source_ip, user_agent, context_json "
                    "FROM weekly_digest_feedback"
                ).fetchone()
                assert row[0] == "pub-abc"
                assert row[1] == "down"
                assert row[2] == "127.0.0.1"
                assert row[3] == "pytest-agent"
                assert '"timestamp": 123' in row[4]
            finally:
                conn.close()

    def test_repeat_vote_same_voter_is_deduped(self):
        """A repeat vote for the same (pub, week, ip) replaces the old row."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "feedback.db")
            for vote in ("up", "up", "down"):
                assert log_publication_feedback(
                    week_start="2026-01-19",
                    week_end="2026-01-25",
                    publication_id="pub-abc",
                    vote=vote,
                    source_ip="10.0.0.1",
                    db_path=db_path,
                ) is True

            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    "SELECT vote FROM weekly_digest_feedback"
                ).fetchall()
                assert len(rows) == 1
                assert rows[0][0] == "down"  # latest vote wins
            finally:
                conn.close()

    def test_different_voters_not_deduped(self):
        """Votes from different IPs for the same pub/week are kept."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "feedback.db")
            for ip in ("10.0.0.1", "10.0.0.2"):
                assert log_publication_feedback(
                    week_start="2026-01-19",
                    week_end="2026-01-25",
                    publication_id="pub-abc",
                    vote="up",
                    source_ip=ip,
                    db_path=db_path,
                ) is True

            conn = sqlite3.connect(db_path)
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM weekly_digest_feedback"
                ).fetchone()[0]
                assert count == 2
            finally:
                conn.close()

    def test_votes_without_source_ip_are_inserted(self):
        """Anonymous votes (no IP) cannot be deduped and are all kept."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "feedback.db")
            for _ in range(2):
                assert log_publication_feedback(
                    week_start="2026-01-19",
                    week_end="2026-01-25",
                    publication_id="pub-abc",
                    vote="up",
                    source_ip=None,
                    db_path=db_path,
                ) is True

            conn = sqlite3.connect(db_path)
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM weekly_digest_feedback"
                ).fetchone()[0]
                assert count == 2
            finally:
                conn.close()


class TestFeedbackReceiverTwoStep:
    """Tests for the two-step (GET confirm -> POST record) feedback flow.

    GET must never record a vote: corporate email link-scanners prefetch
    GET links and used to auto-vote.
    """

    SECRET = "test-receiver-secret"

    def _serve(self, db_path):
        import threading
        from http.server import HTTPServer
        from scripts.collect_digest_feedback import make_handler

        handler = make_handler(
            secret=self.SECRET,
            max_age_seconds=3600,
            db_path=db_path,
            database_url=None,
        )
        server = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, server.server_address[1]

    def _signed_url(self, port, vote="up"):
        return build_feedback_url(
            base_url=f"http://127.0.0.1:{port}/feedback",
            publication_id="pub-1",
            week_start="2026-06-01",
            week_end="2026-06-07",
            vote=vote,
            secret=self.SECRET,
        )

    def _post(self, port, query, expect_error=None):
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/feedback",
            data=query.encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            return urllib.request.urlopen(req).read().decode()
        except urllib.error.HTTPError as e:
            if expect_error is None:
                raise
            assert e.code == expect_error
            return None

    def _vote_rows(self, db_path):
        conn = sqlite3.connect(db_path)
        try:
            return conn.execute(
                "SELECT publication_id, vote FROM weekly_digest_feedback"
            ).fetchall()
        except sqlite3.OperationalError:
            return []  # table not created -> no votes recorded
        finally:
            conn.close()

    def test_get_shows_form_without_recording_vote(self):
        import urllib.request

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "fb.db")
            server, port = self._serve(db_path)
            try:
                body = urllib.request.urlopen(self._signed_url(port)).read().decode()
            finally:
                server.shutdown()

            assert '<form method="post"' in body
            assert "Confirm Thumbs Up" in body
            assert self._vote_rows(db_path) == []

    def test_post_records_vote(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "fb.db")
            server, port = self._serve(db_path)
            try:
                query = self._signed_url(port).split("?", 1)[1]
                body = self._post(port, query)
            finally:
                server.shutdown()

            assert "has been recorded" in body
            assert self._vote_rows(db_path) == [("pub-1", "up")]

    def test_repeat_post_replaces_vote(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "fb.db")
            server, port = self._serve(db_path)
            try:
                self._post(port, self._signed_url(port, vote="up").split("?", 1)[1])
                self._post(port, self._signed_url(port, vote="down").split("?", 1)[1])
            finally:
                server.shutdown()

            assert self._vote_rows(db_path) == [("pub-1", "down")]

    def test_post_with_tampered_signature_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "fb.db")
            server, port = self._serve(db_path)
            try:
                query = self._signed_url(port, vote="down").split("?", 1)[1]
                self._post(port, query.replace("v=down", "v=up"), expect_error=403)
            finally:
                server.shutdown()

            assert self._vote_rows(db_path) == []


class TestRenderDigestAutoescape:
    """Tests for per-extension autoescape in render_digest."""

    def _data(self, title):
        return {
            "week_start": "2026-01-19",
            "week_end": "2026-01-25",
            "total_candidates": 1,
            "scoring_method": "relevancy_only",
            "must_reads": [
                {
                    "title": title,
                    "source": "Nature",
                    "venue": "Nature",
                    "published_date": "2026-01-20",
                    "link": "https://example.com/article",
                    "relevancy_score": 85,
                    "credibility_score": 70,
                    "relevancy_ordinal": "High",
                    "credibility_ordinal": "Moderate",
                    "why_it_matters": "Matters because of A & B.",
                    "summary": "Summary.",
                    "key_findings": [],
                    "commercial_signals": {},
                },
            ],
            "honorable_mentions": [],
            "feedback_enabled": False,
        }

    def test_html_escaped_text_not_escaped(self):
        from scripts.generate_weekly_digest import render_digest

        html, text = render_digest(self._data("BRCA1 & p53 <study>"))

        # HTML part: escaped
        assert "BRCA1 &amp; p53 &lt;study&gt;" in html
        assert "<study>" not in html

        # Text part: raw, no HTML entities
        assert "BRCA1 & p53 <study>" in text
        assert "&amp;" not in text
        assert "&lt;" not in text
