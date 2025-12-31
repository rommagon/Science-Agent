"""Unit tests for AI reranker robust JSON parsing."""

import pytest
from mcp_server.ai_reranker import (
    _robust_parse_ranked_ids,
    _validate_and_repair_ranked_ids,
    _safe_preview,
)


class TestSafePreview:
    """Test safe preview function."""

    def test_safe_preview_normal_text(self):
        text = "This is normal text"
        assert _safe_preview(text) == text

    def test_safe_preview_long_text(self):
        text = "x" * 500
        preview = _safe_preview(text, max_len=100)
        assert len(preview) <= 103  # 100 + "..."
        assert preview.endswith("...")

    def test_safe_preview_redacts_api_keys(self):
        text = "API key: sk-1234567890abcdefghijklmnop here"
        preview = _safe_preview(text)
        assert "sk-1234567890" not in preview
        assert "[REDACTED_KEY]" in preview

    def test_safe_preview_empty(self):
        assert _safe_preview("") == "[empty]"
        assert _safe_preview(None) == "[empty]"


class TestRobustParseRankedIds:
    """Test robust parsing with various input formats."""

    def test_parse_plain_json(self):
        """Test parsing clean JSON."""
        response = '{"ranked_ids": ["id1", "id2", "id3"]}'
        result = _robust_parse_ranked_ids(response, 3)
        assert result == ["id1", "id2", "id3"]

    def test_parse_json_with_whitespace(self):
        """Test parsing JSON with extra whitespace."""
        response = '''
        {
            "ranked_ids": [
                "id1",
                "id2",
                "id3"
            ]
        }
        '''
        result = _robust_parse_ranked_ids(response, 3)
        assert result == ["id1", "id2", "id3"]

    def test_parse_markdown_code_fence(self):
        """Test parsing JSON inside markdown code fence."""
        response = '''```json
{"ranked_ids": ["id1", "id2", "id3"]}
```'''
        result = _robust_parse_ranked_ids(response, 3)
        assert result == ["id1", "id2", "id3"]

    def test_parse_markdown_code_fence_no_language(self):
        """Test parsing JSON inside code fence without language."""
        response = '''```
{"ranked_ids": ["id1", "id2", "id3"]}
```'''
        result = _robust_parse_ranked_ids(response, 3)
        assert result == ["id1", "id2", "id3"]

    def test_parse_json_with_leading_text(self):
        """Test parsing JSON with leading prose."""
        response = '''Here are the ranked IDs:
{"ranked_ids": ["id1", "id2", "id3"]}'''
        result = _robust_parse_ranked_ids(response, 3)
        assert result == ["id1", "id2", "id3"]

    def test_parse_json_with_trailing_text(self):
        """Test parsing JSON with trailing prose."""
        response = '{"ranked_ids": ["id1", "id2", "id3"]} - done!'
        result = _robust_parse_ranked_ids(response, 3)
        assert result == ["id1", "id2", "id3"]

    def test_parse_truncated_json_missing_brace(self):
        """Test parsing truncated JSON (missing final brace)."""
        response = '{"ranked_ids": ["id1", "id2", "id3"]'
        # Should extract via regex
        result = _robust_parse_ranked_ids(response, 3)
        assert result == ["id1", "id2", "id3"]

    def test_parse_truncated_json_mid_array(self):
        """Test parsing truncated JSON mid-array."""
        response = '{"ranked_ids": ["id1", "id2", "id'
        # Should extract what's available
        result = _robust_parse_ranked_ids(response, 2)
        assert result == ["id1", "id2"]

    def test_parse_regex_fallback(self):
        """Test regex extraction fallback."""
        response = 'The ranked_ids are: "ranked_ids": ["id1", "id2"] and done'
        result = _robust_parse_ranked_ids(response, 2)
        assert result == ["id1", "id2"]

    def test_parse_empty_response(self):
        """Test parsing empty response."""
        result = _robust_parse_ranked_ids("", 0)
        assert result is None

    def test_parse_invalid_json(self):
        """Test parsing completely invalid input."""
        response = "This is not JSON at all"
        result = _robust_parse_ranked_ids(response, 0)
        assert result is None


class TestValidateAndRepairRankedIds:
    """Test validation and repair of ranked_ids."""

    def test_validate_perfect_match(self):
        """Test validation with perfect match."""
        ranked_ids = ["id1", "id2", "id3"]
        candidate_ids = ["id1", "id2", "id3"]
        result = _validate_and_repair_ranked_ids(ranked_ids, candidate_ids)
        assert result == ["id1", "id2", "id3"]

    def test_validate_different_order(self):
        """Test validation with different order (should preserve LLM order)."""
        ranked_ids = ["id3", "id1", "id2"]
        candidate_ids = ["id1", "id2", "id3"]
        result = _validate_and_repair_ranked_ids(ranked_ids, candidate_ids)
        assert result == ["id3", "id1", "id2"]

    def test_repair_missing_ids(self):
        """Test repair when LLM misses some IDs."""
        ranked_ids = ["id1", "id3"]  # Missing id2
        candidate_ids = ["id1", "id2", "id3"]
        result = _validate_and_repair_ranked_ids(ranked_ids, candidate_ids)
        # Should append missing id2
        assert result == ["id1", "id3", "id2"]

    def test_repair_unknown_ids(self):
        """Test repair when LLM includes unknown IDs."""
        ranked_ids = ["id1", "id999", "id2", "id3"]
        candidate_ids = ["id1", "id2", "id3"]
        result = _validate_and_repair_ranked_ids(ranked_ids, candidate_ids)
        # Should drop id999
        assert result == ["id1", "id2", "id3"]

    def test_repair_duplicates(self):
        """Test repair when LLM has duplicates."""
        ranked_ids = ["id1", "id2", "id1", "id3"]
        candidate_ids = ["id1", "id2", "id3"]
        result = _validate_and_repair_ranked_ids(ranked_ids, candidate_ids)
        # Should keep first occurrence only
        assert result == ["id1", "id2", "id3"]

    def test_repair_complex_case(self):
        """Test repair with duplicates, unknowns, and missing."""
        ranked_ids = ["id1", "id999", "id1", "id3"]  # Duplicate id1, unknown id999, missing id2
        candidate_ids = ["id1", "id2", "id3"]
        result = _validate_and_repair_ranked_ids(ranked_ids, candidate_ids)
        # Should have id1, id3, then append id2
        assert result == ["id1", "id3", "id2"]

    def test_validate_empty_ranked_ids(self):
        """Test validation with empty ranked_ids."""
        result = _validate_and_repair_ranked_ids([], ["id1", "id2"])
        assert result is None

    def test_validate_empty_candidates(self):
        """Test validation with empty candidates."""
        result = _validate_and_repair_ranked_ids(["id1"], [])
        assert result is None

    def test_repair_all_unknown(self):
        """Test repair when all IDs are unknown."""
        ranked_ids = ["id999", "id888"]
        candidate_ids = ["id1", "id2", "id3"]
        result = _validate_and_repair_ranked_ids(ranked_ids, candidate_ids)
        # Should append all candidates
        assert set(result) == {"id1", "id2", "id3"}
        assert len(result) == 3


class TestIntegration:
    """Integration tests combining parsing and validation."""

    def test_full_pipeline_clean_response(self):
        """Test full pipeline with clean response."""
        response = '{"ranked_ids": ["id1", "id2", "id3"]}'
        candidate_ids = ["id1", "id2", "id3"]

        # Parse
        ranked_ids = _robust_parse_ranked_ids(response, len(candidate_ids))
        assert ranked_ids is not None

        # Validate
        validated = _validate_and_repair_ranked_ids(ranked_ids, candidate_ids)
        assert validated == ["id1", "id2", "id3"]

    def test_full_pipeline_truncated_response(self):
        """Test full pipeline with truncated response."""
        response = '{"ranked_ids": ["id1", "id2"'  # Truncated
        candidate_ids = ["id1", "id2", "id3"]

        # Parse (regex should extract ["id1", "id2"])
        ranked_ids = _robust_parse_ranked_ids(response, len(candidate_ids))
        assert ranked_ids is not None
        assert "id1" in ranked_ids
        assert "id2" in ranked_ids

        # Validate (should append missing id3)
        validated = _validate_and_repair_ranked_ids(ranked_ids, candidate_ids)
        assert set(validated) == {"id1", "id2", "id3"}

    def test_full_pipeline_markdown_with_extras(self):
        """Test full pipeline with markdown and extra IDs."""
        response = '''```json
{"ranked_ids": ["id1", "id999", "id2", "id3"]}
```'''
        candidate_ids = ["id1", "id2", "id3"]

        # Parse
        ranked_ids = _robust_parse_ranked_ids(response, len(candidate_ids))
        assert ranked_ids is not None

        # Validate (should drop id999)
        validated = _validate_and_repair_ranked_ids(ranked_ids, candidate_ids)
        assert validated == ["id1", "id2", "id3"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
