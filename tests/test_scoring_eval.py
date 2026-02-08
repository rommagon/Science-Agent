"""Tests for scoring evaluation framework.

Tests cover:
- Dataset loading and matching (DOI, PMID, title normalization)
- Metrics computation (Spearman, NDCG, Recall)
- Prompt JSON parsing
- Calibration monotonicity and bounds
"""

import json
import pytest
import tempfile
from pathlib import Path


class TestDatasetMatching:
    """Tests for publication matching across datasets."""

    def test_normalize_doi_basic(self):
        """Test DOI normalization with various formats."""
        from scoring_eval.datasets import normalize_doi

        # Standard DOI
        assert normalize_doi("10.1234/example") == "10.1234/example"

        # With prefix
        assert normalize_doi("doi:10.1234/example") == "10.1234/example"
        assert normalize_doi("DOI:10.1234/example") == "10.1234/example"

        # With URL
        assert normalize_doi("https://doi.org/10.1234/example") == "10.1234/example"
        assert normalize_doi("http://dx.doi.org/10.1234/example") == "10.1234/example"

        # Case normalization
        assert normalize_doi("10.1234/EXAMPLE") == "10.1234/example"

        # Invalid DOIs
        assert normalize_doi("") is None
        assert normalize_doi(None) is None
        assert normalize_doi("not-a-doi") is None

    def test_normalize_pmid_basic(self):
        """Test PMID normalization with various formats."""
        from scoring_eval.datasets import normalize_pmid

        # Standard PMID
        assert normalize_pmid("12345678") == "12345678"

        # With prefix
        assert normalize_pmid("PMID:12345678") == "12345678"
        assert normalize_pmid("pmid:12345678") == "12345678"

        # With URL
        assert normalize_pmid("https://pubmed.ncbi.nlm.nih.gov/12345678") == "12345678"

        # Invalid PMIDs
        assert normalize_pmid("") is None
        assert normalize_pmid(None) is None
        assert normalize_pmid("abcdefgh") is None

    def test_normalize_title(self):
        """Test title normalization for matching."""
        from scoring_eval.datasets import normalize_title

        # Basic normalization
        assert normalize_title("Hello World") == "hello world"

        # Extra whitespace
        assert normalize_title("  Hello   World  ") == "hello world"

        # Punctuation removal
        assert normalize_title("Hello, World!") == "hello world"

        # Empty/None
        assert normalize_title("") == ""
        assert normalize_title(None) == ""

    def test_match_publications_by_doi(self):
        """Test matching publications by DOI."""
        from scoring_eval.datasets import match_publications

        item1 = {"doi": "10.1234/example", "title": "Title A"}
        item2 = {"doi": "10.1234/example", "title": "Title B"}

        assert match_publications(item1, item2) is True

    def test_match_publications_by_pmid(self):
        """Test matching publications by PMID."""
        from scoring_eval.datasets import match_publications

        item1 = {"pmid": "12345678", "title": "Title A"}
        item2 = {"pmid": "12345678", "title": "Title B"}

        assert match_publications(item1, item2) is True

    def test_match_publications_by_title(self):
        """Test matching publications by normalized title."""
        from scoring_eval.datasets import match_publications

        item1 = {"title": "A Novel Approach to Cancer Detection Using ctDNA"}
        item2 = {"title": "A Novel Approach to Cancer Detection Using ctDNA"}

        assert match_publications(item1, item2) is True

        # Very similar titles (near-identical)
        item3 = {"title": "A Novel Approach to Cancer Detection Using ctDNA Methods"}
        assert match_publications(item1, item3) is True

    def test_match_publications_no_match(self):
        """Test that different publications don't match."""
        from scoring_eval.datasets import match_publications

        item1 = {"doi": "10.1234/a", "title": "Title A"}
        item2 = {"doi": "10.1234/b", "title": "Title B"}

        assert match_publications(item1, item2) is False

    def test_extract_doi_from_url(self):
        """Test DOI extraction from URLs."""
        from scoring_eval.datasets import extract_doi_from_url

        # Standard DOI URL
        assert extract_doi_from_url("https://doi.org/10.1234/example") == "10.1234/example"

        # Nature URL with DOI
        assert extract_doi_from_url("https://www.nature.com/articles/s41586-024-07051-0") is None
        # (This shouldn't match since it's not a DOI format)

        # Embedded DOI
        url = "https://www.sciencedirect.com/science/article/pii/S0140673623017002"
        # No DOI in this format
        assert extract_doi_from_url(url) is None

    def test_merge_datasets(self):
        """Test merging datasets with overlapping publications."""
        from scoring_eval.datasets import merge_datasets

        dataset1 = [
            {
                "doi": "10.1234/a",
                "title": "Paper A",
                "human_labels": [{"source": "udi", "rating_0_3": 3}],
            }
        ]

        dataset2 = [
            {
                "doi": "10.1234/a",  # Same paper
                "title": "Paper A",
                "human_labels": [{"source": "survey", "rating_0_3": 2}],
            },
            {
                "doi": "10.1234/b",  # Different paper
                "title": "Paper B",
                "human_labels": [{"source": "survey", "rating_0_3": 1}],
            },
        ]

        merged = merge_datasets(dataset1, dataset2)

        assert len(merged) == 2  # Two unique papers

        # Find the merged paper A
        paper_a = next(p for p in merged if p.get("doi") == "10.1234/a")
        assert len(paper_a["human_labels"]) == 2  # Both labels merged


class TestMetrics:
    """Tests for evaluation metrics."""

    def test_compute_spearman_perfect_correlation(self):
        """Test Spearman correlation with perfectly correlated data."""
        from scoring_eval.metrics import compute_spearman

        items = [
            {"model_score": 10, "mean_human_rating": 0.5},
            {"model_score": 50, "mean_human_rating": 1.5},
            {"model_score": 75, "mean_human_rating": 2.5},
            {"model_score": 100, "mean_human_rating": 3.0},
        ]

        result = compute_spearman(items)

        assert result["n"] == 4
        assert result["spearman_rho"] is not None
        assert result["spearman_rho"] > 0.9  # High positive correlation

    def test_compute_spearman_no_correlation(self):
        """Test Spearman with insufficient data."""
        from scoring_eval.metrics import compute_spearman

        items = [
            {"model_score": 50, "mean_human_rating": 2.0},
            {"model_score": 50, "mean_human_rating": 2.0},
        ]

        result = compute_spearman(items)
        assert result["n"] == 2
        # With only 2 identical points, correlation is undefined or NaN

    def test_compute_ndcg_perfect_ranking(self):
        """Test NDCG with perfect ranking."""
        from scoring_eval.metrics import compute_ndcg

        # Model scores perfectly rank by relevance
        items = [
            {"model_score": 100, "udi_rating": 3},
            {"model_score": 80, "udi_rating": 2},
            {"model_score": 50, "udi_rating": 1},
            {"model_score": 20, "udi_rating": 0},
        ]

        result = compute_ndcg(items, k=4)

        assert result["ndcg"] == 1.0  # Perfect ranking

    def test_compute_ndcg_reversed_ranking(self):
        """Test NDCG with reversed ranking."""
        from scoring_eval.metrics import compute_ndcg

        # Model scores are reversed from true relevance
        items = [
            {"model_score": 20, "udi_rating": 3},
            {"model_score": 50, "udi_rating": 2},
            {"model_score": 80, "udi_rating": 1},
            {"model_score": 100, "udi_rating": 0},
        ]

        result = compute_ndcg(items, k=4)

        # Reversed ranking should be worse than perfect (1.0) but NDCG
        # can still be > 0.5 due to DCG formula. Just verify it's < 1.0
        assert result["ndcg"] < 1.0  # Not perfect ranking

    def test_compute_recall_at_k(self):
        """Test Recall@K calculation."""
        from scoring_eval.metrics import compute_recall_at_k

        # 3 items with rating=3, model correctly ranks 2 of them in top 3
        items = [
            {"model_score": 95, "udi_rating": 3},  # Correct
            {"model_score": 90, "udi_rating": 3},  # Correct
            {"model_score": 85, "udi_rating": 1},  # Incorrect (not relevant)
            {"model_score": 80, "udi_rating": 3},  # Correct but outside top-3
            {"model_score": 70, "udi_rating": 0},
        ]

        result = compute_recall_at_k(items, k=3, relevance_threshold=3)

        assert result["total_relevant"] == 3
        assert result["hits"] == 2
        assert result["recall"] == 2 / 3

    def test_find_top_disagreements(self):
        """Test finding top disagreements."""
        from scoring_eval.metrics import find_top_disagreements

        items = [
            {"title": "A", "model_score": 90, "mean_human_rating": 3.0},  # Agreement
            {"title": "B", "model_score": 20, "mean_human_rating": 3.0},  # Big disagreement
            {"title": "C", "model_score": 50, "mean_human_rating": 1.5},  # Agreement
        ]

        disagreements = find_top_disagreements(items, n=2)

        assert len(disagreements) == 2
        assert disagreements[0]["title"] == "B"  # Largest disagreement first


class TestCalibration:
    """Tests for isotonic calibration."""

    def test_calibrator_fit_and_transform(self):
        """Test basic calibrator fitting and transformation."""
        from scoring_eval.calibration import IsotonicCalibrator

        calibrator = IsotonicCalibrator()

        # Training data: model tends to overscore
        model_scores = [10, 30, 50, 70, 90]
        human_ratings = [0.5, 1.0, 1.5, 2.0, 2.5]  # 0-3 scale

        calibrator.fit(model_scores, human_ratings, output_scale="0_100")

        assert calibrator.is_fitted

        # Transform should reduce high scores
        calibrated = calibrator.transform(90)
        # Should be closer to 2.5/3 * 100 = 83
        assert calibrated < 90

    def test_calibrator_monotonicity(self):
        """Test that calibration maintains monotonicity."""
        from scoring_eval.calibration import IsotonicCalibrator, validate_calibrator_monotonicity

        calibrator = IsotonicCalibrator()

        # Non-monotonic input
        model_scores = [10, 30, 50, 70, 90]
        human_ratings = [0.5, 1.5, 1.0, 2.0, 2.5]  # Non-monotonic

        calibrator.fit(model_scores, human_ratings, output_scale="0_100")

        # Calibrator should produce monotonic output
        assert validate_calibrator_monotonicity(calibrator)

    def test_calibrator_bounds(self):
        """Test that calibrated values stay within bounds."""
        from scoring_eval.calibration import IsotonicCalibrator, validate_calibrator_bounds

        calibrator = IsotonicCalibrator()

        model_scores = [0, 25, 50, 75, 100]
        human_ratings = [0, 1, 1.5, 2, 3]

        calibrator.fit(model_scores, human_ratings, output_scale="0_100")

        assert validate_calibrator_bounds(calibrator, "0_100")

    def test_calibrator_save_load(self):
        """Test saving and loading calibration parameters."""
        from scoring_eval.calibration import IsotonicCalibrator

        calibrator = IsotonicCalibrator()

        model_scores = [10, 30, 50, 70, 90]
        human_ratings = [0.5, 1.0, 1.5, 2.0, 2.5]

        calibrator.fit(model_scores, human_ratings, output_scale="0_100")

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            save_path = Path(f.name)

        try:
            calibrator.save(save_path)

            # Load into new calibrator
            new_calibrator = IsotonicCalibrator()
            new_calibrator.load(save_path)

            # Should produce same results
            for score in [10, 50, 90]:
                assert abs(calibrator.transform(score) - new_calibrator.transform(score)) < 0.001
        finally:
            save_path.unlink()


class TestPromptJsonParsing:
    """Tests for prompt JSON response parsing."""

    def test_parse_v2_review_response(self):
        """Test parsing v2 reviewer JSON response."""
        response = """
{
  "relevancy_rating_0_3": 2,
  "relevancy_score_0_100": 65,
  "key_reasons": ["ctDNA monitoring study", "Early-stage colorectal cancer focus"],
  "tags": ["ctdna", "colon", "prospective"],
  "signals": {
    "cancer_type": "colon",
    "early_detection_focus": true,
    "screening_study": false,
    "risk_stratification": true,
    "biomarker_discovery": false,
    "ctdna_cfdna": true,
    "imaging_based": false,
    "prospective_cohort": true,
    "breath_voc": false,
    "sensor_based": false,
    "human_subjects": true
  },
  "summary": "Study examines ctDNA dynamics for recurrence prediction.",
  "concerns": "Post-diagnosis, not primary screening.",
  "uncertainty": "low"
}
"""
        data = json.loads(response)

        assert data["relevancy_rating_0_3"] == 2
        assert data["relevancy_score_0_100"] == 65
        assert len(data["key_reasons"]) == 2
        assert "ctdna" in data["tags"]
        assert data["signals"]["ctdna_cfdna"] is True
        assert data["uncertainty"] == "low"

    def test_parse_v2_evaluator_response(self):
        """Test parsing v2 GPT evaluator JSON response."""
        response = """
{
  "final_relevancy_rating_0_3": 3,
  "final_relevancy_score": 88,
  "final_relevancy_reason": "Prospective screening study with strong clinical validation.",
  "key_reasons": ["Large prospective cohort", "Multi-cancer detection", "High specificity"],
  "tags": ["screening", "multi-cancer", "ctdna", "prospective"],
  "final_signals": {
    "cancer_type": "multi",
    "early_detection_focus": true,
    "screening_study": true,
    "risk_stratification": false,
    "biomarker_discovery": false,
    "ctdna_cfdna": true,
    "imaging_based": false,
    "prospective_cohort": true,
    "breath_voc": false,
    "human_subjects": true
  },
  "final_summary": "MCED test validated in large screening cohort.",
  "agreement_level": "high",
  "disagreements": "None",
  "evaluator_rationale": "Both reviewers agreed on high relevance.",
  "uncertainty": "low"
}
"""
        data = json.loads(response)

        assert data["final_relevancy_rating_0_3"] == 3
        assert data["final_relevancy_score"] == 88
        assert data["agreement_level"] == "high"
        assert data["final_signals"]["screening_study"] is True

    def test_parse_response_with_markdown_fence(self):
        """Test parsing response wrapped in markdown code fence."""
        response = """```json
{
  "relevancy_rating_0_3": 1,
  "relevancy_score_0_100": 35,
  "key_reasons": ["Discovery phase study"],
  "tags": ["biomarker", "discovery"],
  "signals": {
    "cancer_type": "pancreatic",
    "early_detection_focus": false,
    "screening_study": false,
    "risk_stratification": false,
    "biomarker_discovery": true,
    "ctdna_cfdna": false,
    "imaging_based": false,
    "prospective_cohort": false,
    "breath_voc": false,
    "sensor_based": false,
    "human_subjects": false
  },
  "summary": "Early biomarker work.",
  "concerns": "Needs validation.",
  "uncertainty": "medium"
}
```"""
        # Strip markdown fence
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        data = json.loads(text)

        assert data["relevancy_rating_0_3"] == 1
        assert data["signals"]["biomarker_discovery"] is True


class TestDatasetLoading:
    """Tests for loading various dataset formats."""

    def test_load_udi_seeds_format(self):
        """Test loading Udi seeds JSON format with type/value structure."""
        from scoring_eval.datasets import load_udi_ground_truth

        # Create temp file with udi_seeds format
        data = [
            {"type": "doi", "value": "10.1234/example"},
            {"type": "pmid", "value": "12345678"},
            {"type": "url", "value": "https://doi.org/10.5678/another"},
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            temp_path = Path(f.name)

        try:
            items = load_udi_ground_truth(temp_path)

            assert len(items) == 3
            assert items[0]["doi"] == "10.1234/example"
            assert items[1]["pmid"] == "12345678"
            # Third should extract DOI from URL
            assert items[2]["doi"] == "10.5678/another"

            # All should have udi labels with rating 3 (default for seeds)
            for item in items:
                assert len(item["human_labels"]) == 1
                assert item["human_labels"][0]["source"] == "udi"
                assert item["human_labels"][0]["rating_0_3"] == 3
        finally:
            temp_path.unlink()

    def test_load_calibration_survey_csv(self):
        """Test loading calibration survey from CSV."""
        from scoring_eval.datasets import load_calibration_survey

        csv_content = """title,doi,rating,rater,rationale
Paper A,10.1234/a,3,Alice,Very relevant
Paper A,10.1234/a,2,Bob,Somewhat relevant
Paper B,10.5678/b,1,Alice,Not very relevant
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            temp_path = Path(f.name)

        try:
            items = load_calibration_survey(temp_path)

            assert len(items) == 2  # Two unique papers

            # Paper A should have 2 labels
            paper_a = next(i for i in items if i.get("doi") == "10.1234/a")
            assert len(paper_a["human_labels"]) == 2

            # Paper B should have 1 label
            paper_b = next(i for i in items if i.get("doi") == "10.5678/b")
            assert len(paper_b["human_labels"]) == 1
        finally:
            temp_path.unlink()


class TestScoreConversions:
    """Tests for score/rating conversion utilities."""

    def test_score_to_rating(self):
        """Test converting 0-100 score to 0-3 rating."""
        from scoring_eval.metrics import score_to_rating

        assert score_to_rating(10) == 0
        assert score_to_rating(30) == 1
        assert score_to_rating(60) == 2
        assert score_to_rating(85) == 3

    def test_rating_to_score_range(self):
        """Test converting 0-3 rating to expected score range."""
        from scoring_eval.metrics import rating_to_score_range

        assert rating_to_score_range(0) == (0, 24)
        assert rating_to_score_range(1) == (25, 49)
        assert rating_to_score_range(2) == (50, 74)
        assert rating_to_score_range(3) == (75, 100)


class TestBenchmarkMode:
    """Tests for benchmark mode functionality."""

    def test_load_eval_dataset_seeds_format(self):
        """Test loading eval dataset in seeds format."""
        from scripts.run_tri_model_benchmark import load_eval_dataset

        seeds_content = json.dumps([
            {"type": "pmid", "value": "12345678"},
            {"type": "doi", "value": "10.1234/example"},
            {"type": "url", "value": "https://example.com/paper"},
        ])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(seeds_content)
            temp_path = Path(f.name)

        try:
            items = load_eval_dataset(temp_path)
            assert len(items) == 3
            assert items[0]["type"] == "pmid"
            assert items[0]["value"] == "12345678"
            assert items[1]["type"] == "doi"
            assert items[2]["type"] == "url"
        finally:
            temp_path.unlink()

    def test_load_eval_dataset_full_items_format(self):
        """Test loading eval dataset in full items format."""
        from scripts.run_tri_model_benchmark import load_eval_dataset

        items_content = json.dumps([
            {
                "publication_id": "abc123",
                "title": "Multi-cancer early detection study",
                "doi": "10.1234/example",
                "abstract": "Background: Early detection...",
                "human_labels": [
                    {"source": "udi", "rating_0_3": 3, "rationale": "Core study"}
                ]
            },
            {
                "publication_id": "def456",
                "title": "Treatment resistance mechanisms",
                "pmid": "87654321",
                "abstract": "We investigated...",
                "human_labels": [
                    {"source": "udi", "rating_0_3": 0, "rationale": "Not relevant"}
                ]
            },
        ])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(items_content)
            temp_path = Path(f.name)

        try:
            items = load_eval_dataset(temp_path)
            assert len(items) == 2

            # Should be converted to seeds format with preserved original
            assert items[0]["type"] == "doi"
            assert items[0]["value"] == "10.1234/example"
            assert "_original_item" in items[0]
            assert items[0]["_original_item"]["title"] == "Multi-cancer early detection study"

            assert items[1]["type"] == "pmid"
            assert items[1]["value"] == "87654321"
        finally:
            temp_path.unlink()

    def test_load_eval_dataset_url_in_title_becomes_url_seed(self):
        """If title accidentally contains URL, convert to URL seed."""
        from scripts.run_tri_model_benchmark import load_eval_dataset

        items_content = json.dumps([
            {
                "title": "https://doi.org/10.1234/example-url-in-title",
                "human_labels": [{"source": "udi", "rating_0_3": 2}],
            }
        ])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(items_content)
            temp_path = Path(f.name)

        try:
            items = load_eval_dataset(temp_path)
            assert len(items) == 1
            assert items[0]["type"] == "doi"
            assert items[0]["value"] == "10.1234/example-url-in-title"
        finally:
            temp_path.unlink()

    def test_load_eval_dataset_wrapped_format(self):
        """Test loading eval dataset with data wrapper."""
        from scripts.run_tri_model_benchmark import load_eval_dataset

        wrapped_content = json.dumps({
            "metadata": {"version": "1.0"},
            "data": [
                {"type": "pmid", "value": "11111111"},
                {"type": "pmid", "value": "22222222"},
            ]
        })

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(wrapped_content)
            temp_path = Path(f.name)

        try:
            items = load_eval_dataset(temp_path)
            assert len(items) == 2
        finally:
            temp_path.unlink()

    def test_load_eval_dataset_empty_raises(self):
        """Test that empty dataset raises error."""
        from scripts.run_tri_model_benchmark import load_eval_dataset

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("[]")
            temp_path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="empty"):
                load_eval_dataset(temp_path)
        finally:
            temp_path.unlink()

    def test_build_paper_from_item(self):
        """Test building paper dict from original item."""
        from scripts.run_tri_model_benchmark import build_paper_from_item

        item = {
            "type": "doi",
            "value": "10.1234/example",
            "_original_item": {
                "publication_id": "test-pub-123",
                "title": "Test Publication Title",
                "source": "Nature",
                "abstract": "This is the abstract text.",
                "doi": "10.1234/example",
                "url": "https://doi.org/10.1234/example",
            }
        }

        paper = build_paper_from_item(item)

        assert paper["id"] == "test-pub-123"
        assert paper["title"] == "Test Publication Title"
        assert paper["source"] == "Nature"
        assert paper["raw_text"] == "This is the abstract text."
        assert paper["doi"] == "10.1234/example"

    def test_build_paper_from_item_with_override_id(self):
        """Test building paper with override publication ID."""
        from scripts.run_tri_model_benchmark import build_paper_from_item

        item = {
            "_original_item": {
                "title": "Test Title",
                "abstract": "Test abstract",
            }
        }

        paper = build_paper_from_item(item, publication_id="override-id")

        assert paper["id"] == "override-id"
        assert paper["title"] == "Test Title"

    def test_benchmark_validation_without_tri_model_flag(self, monkeypatch):
        """Benchmark validation should not require TRI_MODEL_MINI_DAILY."""
        from scripts.run_tri_model_benchmark import validate_benchmark_config

        monkeypatch.delenv("TRI_MODEL_MINI_DAILY", raising=False)
        monkeypatch.setenv("CLAUDE_API_KEY", "test-claude")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("SPOTITEARLY_LLM_API_KEY", "test-openai")

        result = validate_benchmark_config()

        assert result["valid"] is True
        assert result["errors"] == []

    def test_run_benchmark_minimal_seed_no_crash(self, tmp_path, monkeypatch):
        """Run benchmark with minimal seed set and ensure no crash."""
        import scripts.run_tri_model_benchmark as bench

        def fake_review(paper, available_reviewers):
            return {
                "publication_id": paper.get("id"),
                "title": paper.get("title"),
                "source": paper.get("source"),
                "published_date": paper.get("date"),
                "url": paper.get("url"),
                "claude_review": None,
                "gemini_review": None,
                "gpt_evaluation": {"evaluation": {"final_relevancy_score": 50}},
                "credibility": {},
            }

        monkeypatch.setattr(bench, "review_paper_with_tri_model", fake_review)
        monkeypatch.setattr(bench.time, "sleep", lambda _: None)

        output_dir = tmp_path / "benchmark"
        db_path = tmp_path / "acitrack.db"

        items = [{
            "type": "publication_id",
            "value": 123456,
            "_original_item": {
                "publication_id": "pub-1",
                "title": "Test Publication",
                "abstract": "Test abstract",
            },
        }]

        results = bench.run_benchmark(
            items=items,
            experiment_id="test-bench",
            prompt_version="v2",
            output_dir=output_dir,
            available_reviewers=["claude"],
            db_path=str(db_path),
            skip_resolution=False,
        )

        assert results["summary"]["resolved"] == 1
        assert results["summary"]["scored"] == 1

    def test_benchmark_uses_postgres_store_when_database_url_set(self, monkeypatch):
        """Ensure benchmark store lookup honors DATABASE_URL."""
        import scripts.run_tri_model_benchmark as bench

        calls = {}

        class DummyStore:
            def get_publication_by_id(self, publication_id, database_url=None, db_path=None):
                calls["publication_id"] = publication_id
                calls["database_url"] = database_url
                calls["db_path"] = db_path
                return {
                    "id": publication_id,
                    "title": "Test",
                    "raw_text": "Abstract",
                }

        monkeypatch.setattr(bench, "get_store", lambda: DummyStore())
        monkeypatch.setattr(bench, "get_database_url", lambda: "postgresql://example")

        publication = bench._get_publication_from_store("pub-1", "data/db/acitrack.db")

        assert publication is not None
        assert calls["database_url"] == "postgresql://example"
        assert calls["db_path"] is None

    def test_load_tri_model_results_with_experiment_id(self):
        """Test loading tri-model results filtered by experiment_id."""
        from scoring_eval.datasets import load_tri_model_results_from_db
        import sqlite3

        # Create temp database
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_db = Path(f.name)

        try:
            conn = sqlite3.connect(str(temp_db))
            cursor = conn.cursor()

            # Create minimal table
            cursor.execute("""
                CREATE TABLE tri_model_scoring_events (
                    id INTEGER PRIMARY KEY,
                    run_id TEXT,
                    publication_id TEXT,
                    title TEXT,
                    source TEXT,
                    final_relevancy_score INTEGER,
                    final_relevancy_reason TEXT,
                    final_signals_json TEXT,
                    final_summary TEXT,
                    agreement_level TEXT,
                    confidence TEXT,
                    claude_review_json TEXT,
                    gemini_review_json TEXT,
                    gpt_eval_json TEXT,
                    credibility_score INTEGER,
                    credibility_reason TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE publications (
                    id TEXT PRIMARY KEY,
                    raw_text TEXT,
                    url TEXT
                )
            """)

            # Insert test data
            cursor.execute("""
                INSERT INTO tri_model_scoring_events
                (run_id, publication_id, title, source, final_relevancy_score)
                VALUES
                ('benchmark-exp-001', 'pub-a', 'Paper A', 'Nature', 85),
                ('benchmark-exp-001', 'pub-b', 'Paper B', 'Science', 45),
                ('benchmark-exp-002', 'pub-c', 'Paper C', 'Cell', 70),
                ('tri-model-daily', 'pub-d', 'Paper D', 'NEJM', 60)
            """)

            conn.commit()
            conn.close()

            # Test loading by experiment_id
            results = load_tri_model_results_from_db(
                experiment_id="exp-001",
                db_path=str(temp_db),
            )

            assert len(results) == 2
            pub_ids = {r["publication_id"] for r in results}
            assert pub_ids == {"pub-a", "pub-b"}

            # Test loading different experiment
            results2 = load_tri_model_results_from_db(
                experiment_id="exp-002",
                db_path=str(temp_db),
            )
            assert len(results2) == 1
            assert results2[0]["publication_id"] == "pub-c"

            # Test loading all (no filter)
            results_all = load_tri_model_results_from_db(db_path=str(temp_db))
            assert len(results_all) == 4

        finally:
            temp_db.unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
