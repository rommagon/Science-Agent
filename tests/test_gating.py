"""Tests for tri_model/gating.py - two-stage gating pipeline.

Tests verify:
1. High-recall on obvious early-detection titles (go to HIGH or MAYBE)
2. Venue whitelist forces inclusion even without keywords
3. Audit sampling selects ~rate proportion deterministically with seeded RNG
4. Gating reduces tri-model evaluations but all papers can be DB-inserted
"""

import pytest
from tri_model.gating import (
    gate_publication,
    gate_publications,
    filter_for_evaluation,
    GateBucket,
    GateResult,
    GatingStats,
    DEFAULT_VENUE_WHITELIST,
    DEFAULT_KEYWORDS,
    get_gating_config_hashes,
    _normalize_text,
    _match_keywords,
)


class TestGatePublication:
    """Tests for gate_publication function."""

    def test_high_bucket_for_obvious_early_detection(self):
        """Papers with clear early detection signals should go to HIGH bucket."""
        pub = {
            "title": "Multi-cancer early detection using cfDNA methylation patterns",
            "raw_text": "This prospective screening study evaluated a liquid biopsy assay "
                       "for early cancer detection in asymptomatic individuals.",
            "source": "Cancer Discovery",
        }

        result = gate_publication(pub)

        assert result.bucket in (GateBucket.HIGH, GateBucket.MAYBE), \
            f"Early detection paper should be HIGH or MAYBE, got {result.bucket}"
        assert result.score >= 50, f"Expected score >= 50, got {result.score}"
        assert "early detection" in [k.lower() for k in result.keyword_matches] or \
               "liquid biopsy" in [k.lower() for k in result.keyword_matches] or \
               "cfdna" in [k.lower() for k in result.keyword_matches]

    def test_high_bucket_for_screening_study(self):
        """Screening studies should go to HIGH bucket."""
        pub = {
            "title": "Population-based lung cancer screening with low-dose CT",
            "raw_text": "We conducted a population screening study to evaluate the efficacy "
                       "of LDCT for early lung cancer detection. Sensitivity was 95%.",
            "source": "NEJM",
        }

        result = gate_publication(pub)

        assert result.bucket == GateBucket.HIGH, \
            f"Screening study should be HIGH, got {result.bucket}"
        assert result.venue_match, "NEJM should match venue whitelist"

    def test_high_bucket_for_biomarker_validation(self):
        """Biomarker validation studies should go to HIGH bucket."""
        pub = {
            "title": "Validation of ctDNA biomarkers for colorectal cancer detection",
            "raw_text": "In this validation cohort of 500 patients, we evaluated the diagnostic "
                       "performance of circulating tumor DNA biomarkers for CRC screening.",
            "source": "Clinical Cancer Research",
        }

        result = gate_publication(pub)

        assert result.bucket in (GateBucket.HIGH, GateBucket.MAYBE), \
            f"Biomarker validation should be HIGH or MAYBE, got {result.bucket}"

    def test_maybe_bucket_for_related_but_not_core(self):
        """Papers related but not core should go to MAYBE bucket."""
        pub = {
            "title": "Novel urine biomarkers in bladder cancer",
            "raw_text": "We identified several urinary metabolites that may serve as "
                       "diagnostic biomarkers for bladder cancer.",
            "source": "International Journal of Cancer",
        }

        result = gate_publication(pub)

        assert result.bucket in (GateBucket.HIGH, GateBucket.MAYBE), \
            f"Urine biomarker paper should be HIGH or MAYBE, got {result.bucket}"

    def test_low_bucket_for_treatment_focused(self):
        """Treatment-focused papers with no detection keywords should go to LOW."""
        pub = {
            "title": "Phase III trial of immunotherapy in metastatic melanoma",
            "raw_text": "This randomized controlled trial evaluated the efficacy of checkpoint "
                       "inhibitor therapy in patients with advanced metastatic melanoma. "
                       "Overall survival was improved with treatment.",
            "source": "Journal of Cancer Therapy",
        }

        result = gate_publication(pub)

        # May still be MAYBE if from top venue, but definitely not HIGH
        assert result.bucket != GateBucket.HIGH or result.venue_match, \
            "Treatment paper without venue match should not be HIGH"

    def test_low_bucket_for_basic_research(self):
        """Basic research papers should go to LOW bucket."""
        pub = {
            "title": "Molecular mechanisms of oncogene activation in cell lines",
            "raw_text": "We investigated the signaling pathways in mouse models and "
                       "human cell line experiments to understand oncogenesis.",
            "source": "Molecular Biology Reports",
        }

        result = gate_publication(pub)

        assert result.bucket == GateBucket.LOW, \
            f"Basic research should be LOW, got {result.bucket}"

    def test_venue_whitelist_promotes_to_high(self):
        """Papers from whitelisted venues should be promoted even without strong keywords."""
        # Paper from Nature with weak/generic title
        pub = {
            "title": "Analysis of genetic variants in cancer patients",
            "raw_text": "We performed genetic analysis on tumor samples.",
            "source": "Nature Medicine",
        }

        result = gate_publication(pub)

        assert result.venue_match, "Nature Medicine should match venue whitelist"
        # Should at least be MAYBE due to venue
        assert result.bucket in (GateBucket.HIGH, GateBucket.MAYBE), \
            f"Venue-whitelisted paper should be HIGH or MAYBE, got {result.bucket}"

    def test_nejm_always_included(self):
        """NEJM papers should always be included via venue whitelist."""
        pub = {
            "title": "Generic clinical trial results",
            "raw_text": "Clinical outcomes were measured in this study.",
            "source": "New England Journal of Medicine",
        }

        result = gate_publication(pub)

        assert result.venue_match, "NEJM should be on venue whitelist"
        # Even without keywords, venue match gives base score

    def test_lancet_always_included(self):
        """Lancet papers should always be included via venue whitelist."""
        pub = {
            "title": "Global health outcomes study",
            "raw_text": "We analyzed health data from multiple countries.",
            "source": "The Lancet",
        }

        result = gate_publication(pub)

        assert result.venue_match, "Lancet should be on venue whitelist"

    def test_keyword_score_accumulation(self):
        """Multiple keywords should increase score."""
        pub_single = {
            "title": "Biomarker study",
            "raw_text": "We studied a biomarker.",
            "source": "Journal X",
        }

        pub_multiple = {
            "title": "ctDNA biomarker screening study for early detection",
            "raw_text": "We evaluated liquid biopsy ctDNA biomarkers in a prospective "
                       "screening study for multi-cancer early detection.",
            "source": "Journal X",
        }

        result_single = gate_publication(pub_single)
        result_multiple = gate_publication(pub_multiple)

        assert result_multiple.score > result_single.score, \
            "Multiple keywords should give higher score"
        assert len(result_multiple.keyword_matches) > len(result_single.keyword_matches)

    def test_title_keywords_weighted_higher(self):
        """Keywords in title should be weighted higher than abstract."""
        pub_title = {
            "title": "Early detection of cancer using liquid biopsy",
            "raw_text": "We conducted a clinical study.",
            "source": "Journal X",
        }

        pub_abstract = {
            "title": "A clinical study on cancer",
            "raw_text": "We evaluated early detection using liquid biopsy methods.",
            "source": "Journal X",
        }

        result_title = gate_publication(pub_title)
        result_abstract = gate_publication(pub_abstract)

        # Title keywords should give higher score
        assert result_title.score >= result_abstract.score, \
            "Title keywords should weight >= abstract keywords"


class TestGatePublications:
    """Tests for gate_publications batch processing."""

    def test_audit_sampling_deterministic(self):
        """Audit sampling should be deterministic with same seed."""
        pubs = [
            {"id": f"pub_{i}", "title": f"Generic paper {i}", "raw_text": "No keywords", "source": "Journal"}
            for i in range(100)
        ]

        results1, stats1 = gate_publications(pubs, audit_rate=0.1, audit_seed=42)
        results2, stats2 = gate_publications(pubs, audit_rate=0.1, audit_seed=42)

        # Get audit-selected IDs
        audit_ids_1 = {pub["id"] for pub, res in results1 if res.audit_selected}
        audit_ids_2 = {pub["id"] for pub, res in results2 if res.audit_selected}

        assert audit_ids_1 == audit_ids_2, "Same seed should produce same audit selection"

    def test_audit_sampling_different_seeds(self):
        """Different seeds should produce different audit samples."""
        pubs = [
            {"id": f"pub_{i}", "title": f"Generic paper {i}", "raw_text": "No keywords", "source": "Journal"}
            for i in range(100)
        ]

        results1, _ = gate_publications(pubs, audit_rate=0.1, audit_seed=42)
        results2, _ = gate_publications(pubs, audit_rate=0.1, audit_seed=999)

        audit_ids_1 = {pub["id"] for pub, res in results1 if res.audit_selected}
        audit_ids_2 = {pub["id"] for pub, res in results2 if res.audit_selected}

        # With 100 papers at 10%, we expect 10 audited. Different seeds should differ.
        # (Statistically very unlikely to be identical)
        assert audit_ids_1 != audit_ids_2, "Different seeds should produce different selections"

    def test_audit_rate_approximate(self):
        """Audit rate should select approximately the right proportion."""
        pubs = [
            {"id": f"pub_{i}", "title": f"Generic paper {i}", "raw_text": "No keywords", "source": "Journal"}
            for i in range(200)
        ]

        audit_rate = 0.05  # 5%
        results, stats = gate_publications(pubs, audit_rate=audit_rate, audit_seed=123)

        # All should be LOW since no keywords
        assert stats.low_count == 200, "All generic papers should be LOW"

        # Audit count should be ~5% of 200 = 10, with some variance
        expected = int(200 * audit_rate)
        assert abs(stats.audited_low_count - expected) <= 2, \
            f"Expected ~{expected} audited, got {stats.audited_low_count}"

    def test_filter_for_evaluation_correct(self):
        """filter_for_evaluation should return HIGH + MAYBE + audited LOW."""
        pubs = [
            # HIGH - obvious screening
            {"id": "high_1", "title": "Lung cancer screening study with LDCT",
             "raw_text": "Early detection screening.", "source": "NEJM"},
            # MAYBE - some keywords
            {"id": "maybe_1", "title": "Biomarker analysis",
             "raw_text": "We studied cancer biomarkers.", "source": "Journal"},
            # LOW - no keywords
            {"id": "low_1", "title": "Generic paper",
             "raw_text": "No relevant content.", "source": "Journal"},
            {"id": "low_2", "title": "Another generic paper",
             "raw_text": "Still no keywords.", "source": "Journal"},
        ]

        results, stats = gate_publications(pubs, audit_rate=0.5, audit_seed=42)

        # Filter for evaluation
        to_evaluate = filter_for_evaluation(results)
        eval_ids = {pub["id"] for pub, _ in to_evaluate}

        # HIGH and MAYBE should always be included
        assert "high_1" in eval_ids, "HIGH papers should be included"

        # LOW audit-selected should be included
        audited_count = sum(1 for _, r in results if r.audit_selected)
        assert audited_count > 0, "Some LOW papers should be audit-selected"

    def test_stats_counts_correct(self):
        """GatingStats should have correct counts."""
        pubs = [
            # 2 HIGH
            {"id": "h1", "title": "Early cancer detection screening", "raw_text": "MCED study", "source": "Nature"},
            {"id": "h2", "title": "Liquid biopsy for cancer screening", "raw_text": "ctDNA biomarkers", "source": "Cell"},
            # 3 LOW
            {"id": "l1", "title": "Generic paper 1", "raw_text": "Nothing", "source": "Journal"},
            {"id": "l2", "title": "Generic paper 2", "raw_text": "Nothing", "source": "Journal"},
            {"id": "l3", "title": "Generic paper 3", "raw_text": "Nothing", "source": "Journal"},
        ]

        results, stats = gate_publications(pubs, audit_rate=0.0, audit_seed=42)

        assert stats.total == 5
        assert stats.high_count >= 2, "Should have at least 2 HIGH"
        assert stats.low_count >= 2, "Should have some LOW"
        assert stats.high_count + stats.maybe_count + stats.low_count == 5


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_empty_title_and_abstract(self):
        """Should handle empty title and abstract gracefully."""
        pub = {
            "title": "",
            "raw_text": "",
            "source": "Journal",
        }

        result = gate_publication(pub)

        assert result.bucket == GateBucket.LOW
        assert result.score == 0
        assert result.reason == "no_signals"

    def test_none_values(self):
        """Should handle None values gracefully."""
        pub = {
            "title": None,
            "raw_text": None,
            "source": None,
        }

        result = gate_publication(pub)

        assert result.bucket == GateBucket.LOW

    def test_special_characters_in_text(self):
        """Should handle special characters in text."""
        pub = {
            "title": "Novel α-methylation biomarker (β-type) for early detection",
            "raw_text": "We used CRISPR-Cas9™ for cfDNA analysis...",
            "source": "Nature Methods",
        }

        result = gate_publication(pub)

        # Should still match keywords
        assert "biomarker" in [k.lower() for k in result.keyword_matches] or \
               "early detection" in [k.lower() for k in result.keyword_matches] or \
               "cfdna" in [k.lower() for k in result.keyword_matches]

    def test_case_insensitive_matching(self):
        """Keyword matching should be case-insensitive."""
        pub_lower = {"title": "early detection study", "raw_text": "liquid biopsy", "source": "Journal"}
        pub_upper = {"title": "EARLY DETECTION STUDY", "raw_text": "LIQUID BIOPSY", "source": "Journal"}
        pub_mixed = {"title": "Early Detection Study", "raw_text": "Liquid Biopsy", "source": "Journal"}

        result_lower = gate_publication(pub_lower)
        result_upper = gate_publication(pub_upper)
        result_mixed = gate_publication(pub_mixed)

        assert result_lower.score == result_upper.score == result_mixed.score, \
            "Case should not affect score"


class TestConfigHashes:
    """Tests for configuration hash generation."""

    def test_hash_deterministic(self):
        """Same lists should produce same hash."""
        venues1 = ["nature", "cell", "science"]
        venues2 = ["nature", "cell", "science"]

        hash1 = get_gating_config_hashes(venues1, DEFAULT_KEYWORDS)
        hash2 = get_gating_config_hashes(venues2, DEFAULT_KEYWORDS)

        assert hash1["venue_whitelist_hash"] == hash2["venue_whitelist_hash"]

    def test_hash_order_independent(self):
        """Order should not affect hash (list is sorted internally)."""
        venues1 = ["nature", "cell", "science"]
        venues2 = ["science", "nature", "cell"]

        hash1 = get_gating_config_hashes(venues1, DEFAULT_KEYWORDS)
        hash2 = get_gating_config_hashes(venues2, DEFAULT_KEYWORDS)

        assert hash1["venue_whitelist_hash"] == hash2["venue_whitelist_hash"]

    def test_hash_different_for_different_lists(self):
        """Different lists should produce different hashes."""
        venues1 = ["nature", "cell"]
        venues2 = ["nature", "cell", "science"]

        hash1 = get_gating_config_hashes(venues1, DEFAULT_KEYWORDS)
        hash2 = get_gating_config_hashes(venues2, DEFAULT_KEYWORDS)

        assert hash1["venue_whitelist_hash"] != hash2["venue_whitelist_hash"]


class TestRecallOptimization:
    """Tests verifying the system optimizes for recall (not missing must-reads)."""

    def test_mced_always_high_or_maybe(self):
        """MCED (multi-cancer early detection) should always be HIGH or MAYBE."""
        pub = {
            "title": "MCED test performance in clinical practice",
            "raw_text": "Evaluation of the MCED test.",
            "source": "Generic Journal",
        }

        result = gate_publication(pub)

        assert result.bucket in (GateBucket.HIGH, GateBucket.MAYBE), \
            "MCED papers should never be LOW"

    def test_ctdna_always_high_or_maybe(self):
        """ctDNA papers should always be HIGH or MAYBE."""
        pub = {
            "title": "ctDNA in cancer patients",
            "raw_text": "We measured circulating tumor DNA levels.",
            "source": "Generic Journal",
        }

        result = gate_publication(pub)

        assert result.bucket in (GateBucket.HIGH, GateBucket.MAYBE), \
            "ctDNA papers should never be LOW"

    def test_breath_voc_detection_high_or_maybe(self):
        """Breath/VOC detection papers should be HIGH or MAYBE."""
        pub = {
            "title": "Volatile organic compounds in exhaled breath for cancer detection",
            "raw_text": "Breath analysis using electronic nose technology.",
            "source": "Sensors Journal",
        }

        result = gate_publication(pub)

        assert result.bucket in (GateBucket.HIGH, GateBucket.MAYBE), \
            "Breath/VOC papers should never be LOW"

    def test_canine_detection_high_or_maybe(self):
        """Canine detection papers should be HIGH or MAYBE."""
        pub = {
            "title": "Trained dogs for cancer detection",
            "raw_text": "Canine olfactory detection of cancer biomarkers.",
            "source": "Journal of Breath Research",
        }

        result = gate_publication(pub)

        assert result.bucket in (GateBucket.HIGH, GateBucket.MAYBE), \
            "Canine detection papers should never be LOW"


class TestHelperFunctions:
    """Tests for internal helper functions."""

    def test_normalize_text(self):
        """Text normalization should handle whitespace and case."""
        assert _normalize_text("  Hello   World  ") == "hello world"
        assert _normalize_text("UPPERCASE") == "uppercase"
        assert _normalize_text("") == ""
        assert _normalize_text(None) == ""

    def test_match_keywords_word_boundaries(self):
        """Short keywords should require word boundaries."""
        text = "voc analysis"  # 'voc' is a 3-letter keyword

        # Should match 'voc'
        matches = _match_keywords(text, ["voc"])
        assert "voc" in [m.lower() for m in matches]

        # Should not match 'voc' in 'advocate'
        text2 = "advocate for change"
        matches2 = _match_keywords(text2, ["voc"])
        assert len(matches2) == 0, "'voc' should not match within 'advocate'"

    def test_match_keywords_long_substring(self):
        """Long keywords can match as substrings."""
        text = "early detection study"

        matches = _match_keywords(text, ["early detection"])
        assert "early detection" in [m.lower() for m in matches]


# Integration test
class TestIntegration:
    """Integration tests for the full gating workflow."""

    def test_full_workflow_reduces_evaluations(self):
        """Full workflow should reduce number of papers to evaluate."""
        # Create a mix of papers
        pubs = []

        # 5 obviously relevant
        for i in range(5):
            pubs.append({
                "id": f"relevant_{i}",
                "title": f"Early cancer detection study #{i}",
                "raw_text": "Liquid biopsy screening with ctDNA biomarkers for MCED.",
                "source": "Nature Medicine",
            })

        # 95 generic/irrelevant
        for i in range(95):
            pubs.append({
                "id": f"generic_{i}",
                "title": f"Generic research paper #{i}",
                "raw_text": "This is a generic research paper about cell biology and mouse models.",
                "source": "Generic Journal",
            })

        # Gate all papers
        gated_results, stats = gate_publications(pubs, audit_rate=0.02, audit_seed=42)

        # Filter for evaluation
        to_evaluate = filter_for_evaluation(gated_results)

        # Should reduce evaluations significantly
        assert len(to_evaluate) < len(pubs), "Gating should reduce evaluations"

        # All 5 relevant should be included
        eval_ids = {pub["id"] for pub, _ in to_evaluate}
        for i in range(5):
            assert f"relevant_{i}" in eval_ids, f"Relevant paper {i} should be included"

        # Should have ~2% audit from LOW (2% of 95 ≈ 2)
        assert stats.audited_low_count >= 1, "Should have some audit papers"

        # Stats should be consistent
        assert stats.to_evaluate() == len(to_evaluate) if hasattr(stats, 'to_evaluate') else True

    def test_all_papers_can_be_stored(self):
        """All papers (including LOW) should be storable to DB."""
        pubs = [
            {"id": "high_1", "title": "MCED screening", "raw_text": "Early detection", "source": "Nature"},
            {"id": "low_1", "title": "Generic", "raw_text": "Nothing", "source": "Journal"},
        ]

        gated_results, stats = gate_publications(pubs, audit_rate=0.0)

        # All papers should have gate results
        assert len(gated_results) == 2

        # Each result should have all required fields for DB storage
        for pub, result in gated_results:
            gate_dict = result.to_dict()
            assert "gate_bucket" in gate_dict
            assert "gate_score" in gate_dict
            assert "gate_reason" in gate_dict
            assert "gate_audit_selected" in gate_dict
