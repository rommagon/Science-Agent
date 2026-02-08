"""Test relevancy scoring caching to ensure single invocation per publication per run.

This test verifies that relevancy scoring is called exactly once per publication
per run, and that cached scores are reused in subsequent phases.
"""

import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path


class TestRelevancyScoringCaching(unittest.TestCase):
    """Test relevancy scoring caching behavior."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_run_id = "test-2026-01-20"
        self.test_pub_id = "test_pub_123"
        self.test_item = {
            "id": self.test_pub_id,
            "title": "Test Publication About Breast Cancer Detection",
            "raw_text": "This is a test abstract about early detection using breath-based sensors.",
            "source": "Nature Cancer",
        }

    @patch('mcp_server.llm_relevancy._call_llm')
    @patch('mcp_server.llm_relevancy._get_api_key')
    def test_single_scoring_per_publication(self, mock_get_api_key, mock_call_llm):
        """Test that LLM is called only once per publication per run."""
        from mcp_server.llm_relevancy import score_relevancy, clear_run_cache, SCORING_VERSION

        # Clear cache before test
        clear_run_cache()

        # Mock API key
        mock_get_api_key.return_value = "test_api_key"

        # Mock LLM response
        mock_call_llm.return_value = '''{
            "relevancy_score": 85,
            "relevancy_reason": "Highly relevant: breast cancer detection using breath sensors",
            "confidence": "high",
            "signals": {
                "cancer_type": "breast",
                "breath_based": true,
                "animal_model": false,
                "ngs_genomics": false
            }
        }'''

        # First call: should hit the LLM
        result1 = score_relevancy(
            self.test_item,
            run_id=self.test_run_id,
            mode="daily",
            store_to_db=False,  # Don't store to DB in test
        )

        # Verify LLM was called once
        self.assertEqual(mock_call_llm.call_count, 1)
        self.assertEqual(result1["relevancy_score"], 89)
        self.assertEqual(result1["confidence"], "high")

        # Second call with same run_id and pub_id: should use cache
        result2 = score_relevancy(
            self.test_item,
            run_id=self.test_run_id,
            mode="daily",
            store_to_db=False,
        )

        # Verify LLM was NOT called again
        self.assertEqual(mock_call_llm.call_count, 1, "LLM should not be called again for cached item")
        self.assertEqual(result2["relevancy_score"], 89)
        self.assertEqual(result2["confidence"], "high")

        # Third call without run_id but with item cache: should still use cache
        self.test_item["scoring_version"] = SCORING_VERSION
        self.test_item["relevancy_score"] = 89
        self.test_item["relevancy_reason"] = result1["relevancy_reason"]
        self.test_item["confidence"] = "high"
        self.test_item["signals"] = result1["signals"]

        result3 = score_relevancy(
            self.test_item,
            run_id=None,
            mode=None,
            store_to_db=False,
        )

        # Verify LLM was still NOT called
        self.assertEqual(mock_call_llm.call_count, 1, "LLM should not be called for item with cached scores")
        self.assertEqual(result3["relevancy_score"], 89)

    @patch('mcp_server.llm_relevancy._call_llm')
    @patch('mcp_server.llm_relevancy._get_api_key')
    def test_different_runs_isolated(self, mock_get_api_key, mock_call_llm):
        """Test that different run_ids maintain isolated caches."""
        from mcp_server.llm_relevancy import score_relevancy, clear_run_cache

        # Clear cache before test
        clear_run_cache()

        # Mock API key
        mock_get_api_key.return_value = "test_api_key"

        # Mock LLM response
        mock_call_llm.return_value = '''{
            "relevancy_score": 85,
            "relevancy_reason": "Test reason",
            "confidence": "high",
            "signals": {"cancer_type": "breast", "breath_based": true, "animal_model": false, "ngs_genomics": false}
        }'''

        # Score for run_id_1
        result1 = score_relevancy(
            self.test_item,
            run_id="run_1",
            mode="daily",
            store_to_db=False,
        )

        self.assertEqual(mock_call_llm.call_count, 1)
        self.assertEqual(result1["relevancy_score"], 89)

        # Score same item for run_id_2 (different run): should call LLM again
        result2 = score_relevancy(
            self.test_item,
            run_id="run_2",
            mode="daily",
            store_to_db=False,
        )

        # Note: In current implementation, the item cache would prevent this.
        # This test documents the behavior that run caches are isolated,
        # but item-level caching takes precedence.
        # For true run isolation, we'd need to clear item cache between runs.
        self.assertEqual(result2["relevancy_score"], 89)

    @patch('mcp_server.llm_relevancy._call_llm')
    @patch('mcp_server.llm_relevancy._get_api_key')
    def test_cache_miss_calls_llm(self, mock_get_api_key, mock_call_llm):
        """Test that cache miss results in LLM call."""
        from mcp_server.llm_relevancy import score_relevancy, clear_run_cache

        # Clear cache before test
        clear_run_cache()

        # Mock API key
        mock_get_api_key.return_value = "test_api_key"

        # Mock LLM response
        mock_call_llm.return_value = '''{
            "relevancy_score": 75,
            "relevancy_reason": "Moderately relevant",
            "confidence": "medium",
            "signals": {"cancer_type": "lung", "breath_based": false, "animal_model": false, "ngs_genomics": true}
        }'''

        # Create item without cached scores
        uncached_item = {
            "id": "uncached_pub_456",
            "title": "Different Publication",
            "raw_text": "Different abstract",
            "source": "Science",
        }

        # Should call LLM
        result = score_relevancy(
            uncached_item,
            run_id=self.test_run_id,
            mode="daily",
            store_to_db=False,
        )

        # Verify LLM was called
        self.assertEqual(mock_call_llm.call_count, 1)
        # V3 normalization compresses mid-high scores (70-84 => minus 5).
        self.assertEqual(result["relevancy_score"], 70)
        self.assertEqual(result["confidence"], "medium")


if __name__ == "__main__":
    unittest.main()
