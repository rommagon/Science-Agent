"""Tests for semantic search module."""

import numpy as np
import pytest

from acitrack.semantic_search import (
    build_embedding_text,
    compute_content_hash,
    embedding_to_bytes,
    bytes_to_embedding,
    cosine_similarity,
    get_embedding_dimension,
    DEFAULT_EMBEDDING_MODEL,
)


class TestBuildEmbeddingText:
    """Tests for embedding text generation."""

    def test_build_with_all_fields(self):
        """Test text building with all fields present."""
        pub = {
            "title": "Cancer Detection Study",
            "raw_text": "This study investigates novel biomarkers.",
            "summary": "",
            "source": "Nature Cancer",
            "venue": "Nature",
            "published_date": "2024-01-15",
        }

        text = build_embedding_text(pub)

        assert "Cancer Detection Study" in text
        assert "novel biomarkers" in text
        assert "Nature" in text
        assert "2024-01-15" in text

    def test_build_with_summary_fallback(self):
        """Test that summary is used when raw_text is empty."""
        pub = {
            "title": "Cancer Detection Study",
            "raw_text": "",
            "summary": "A brief summary of the study.",
            "source": "Nature",
            "venue": "",
            "published_date": "",
        }

        text = build_embedding_text(pub)

        assert "Cancer Detection Study" in text
        assert "brief summary" in text

    def test_build_title_only(self):
        """Test text building with only title."""
        pub = {
            "title": "Cancer Detection Study",
            "raw_text": "",
            "summary": "",
            "source": "",
            "venue": "",
            "published_date": "",
        }

        text = build_embedding_text(pub)

        assert text == "Cancer Detection Study"

    def test_build_uses_source_when_venue_empty(self):
        """Test that source is used when venue is empty."""
        pub = {
            "title": "Test",
            "raw_text": "",
            "summary": "",
            "source": "PubMed Cancer",
            "venue": "",
            "published_date": "",
        }

        text = build_embedding_text(pub)

        assert "Source: PubMed Cancer" in text


class TestComputeContentHash:
    """Tests for content hash computation."""

    def test_hash_deterministic(self):
        """Test that same input produces same hash."""
        text = "Test content for hashing"

        hash1 = compute_content_hash(text)
        hash2 = compute_content_hash(text)

        assert hash1 == hash2

    def test_hash_different_for_different_content(self):
        """Test that different content produces different hash."""
        hash1 = compute_content_hash("Content A")
        hash2 = compute_content_hash("Content B")

        assert hash1 != hash2

    def test_hash_case_insensitive(self):
        """Test that hash is case-insensitive (normalized to lowercase)."""
        hash1 = compute_content_hash("Test Content")
        hash2 = compute_content_hash("test content")

        assert hash1 == hash2

    def test_hash_trims_whitespace(self):
        """Test that leading/trailing whitespace is trimmed."""
        hash1 = compute_content_hash("  Test Content  ")
        hash2 = compute_content_hash("Test Content")

        assert hash1 == hash2

    def test_hash_format(self):
        """Test that hash is a valid SHA256 hex string."""
        hash_val = compute_content_hash("Test")

        assert len(hash_val) == 64
        assert all(c in "0123456789abcdef" for c in hash_val)

    def test_hash_changes_on_title_change(self):
        """Test that hash changes when title changes."""
        text1 = "Original Title\n\nAbstract text"
        text2 = "Modified Title\n\nAbstract text"

        hash1 = compute_content_hash(text1)
        hash2 = compute_content_hash(text2)

        assert hash1 != hash2

    def test_hash_changes_on_abstract_change(self):
        """Test that hash changes when abstract changes."""
        text1 = "Title\n\nOriginal abstract text"
        text2 = "Title\n\nModified abstract text"

        hash1 = compute_content_hash(text1)
        hash2 = compute_content_hash(text2)

        assert hash1 != hash2


class TestEmbeddingConversion:
    """Tests for embedding byte conversion."""

    def test_roundtrip_conversion(self):
        """Test that embedding survives round-trip conversion."""
        original = np.array([0.1, 0.2, 0.3, 0.4, 0.5], dtype=np.float32)

        bytes_data = embedding_to_bytes(original)
        recovered = bytes_to_embedding(bytes_data, len(original))

        np.testing.assert_array_almost_equal(original, recovered)

    def test_conversion_preserves_precision(self):
        """Test that float32 precision is preserved."""
        original = np.array([0.123456789, -0.987654321], dtype=np.float32)

        bytes_data = embedding_to_bytes(original)
        recovered = bytes_to_embedding(bytes_data, len(original))

        # Should be equal within float32 precision
        np.testing.assert_array_equal(original, recovered)

    def test_large_embedding_conversion(self):
        """Test conversion of a large embedding vector."""
        # Typical embedding dimension
        dim = 1536
        original = np.random.randn(dim).astype(np.float32)

        bytes_data = embedding_to_bytes(original)
        recovered = bytes_to_embedding(bytes_data, dim)

        np.testing.assert_array_equal(original, recovered)


class TestCosineSimilarity:
    """Tests for cosine similarity computation."""

    def test_identical_vectors(self):
        """Test that identical vectors have similarity 1."""
        vec = np.array([1.0, 2.0, 3.0])

        similarity = cosine_similarity(vec, vec)

        assert similarity == pytest.approx(1.0)

    def test_opposite_vectors(self):
        """Test that opposite vectors have similarity -1."""
        vec1 = np.array([1.0, 0.0, 0.0])
        vec2 = np.array([-1.0, 0.0, 0.0])

        similarity = cosine_similarity(vec1, vec2)

        assert similarity == pytest.approx(-1.0)

    def test_orthogonal_vectors(self):
        """Test that orthogonal vectors have similarity 0."""
        vec1 = np.array([1.0, 0.0, 0.0])
        vec2 = np.array([0.0, 1.0, 0.0])

        similarity = cosine_similarity(vec1, vec2)

        assert similarity == pytest.approx(0.0)

    def test_zero_vector(self):
        """Test that zero vectors return 0 similarity."""
        vec1 = np.array([1.0, 2.0, 3.0])
        vec2 = np.array([0.0, 0.0, 0.0])

        similarity = cosine_similarity(vec1, vec2)

        assert similarity == 0.0

    def test_similar_vectors(self):
        """Test that similar vectors have high similarity."""
        vec1 = np.array([1.0, 2.0, 3.0])
        vec2 = np.array([1.1, 2.1, 3.1])

        similarity = cosine_similarity(vec1, vec2)

        assert similarity > 0.99  # Very similar


class TestEmbeddingDimension:
    """Tests for embedding dimension retrieval."""

    def test_known_model_dimension(self):
        """Test dimension for known models."""
        assert get_embedding_dimension("text-embedding-3-small") == 1536
        assert get_embedding_dimension("text-embedding-3-large") == 3072
        assert get_embedding_dimension("text-embedding-ada-002") == 1536

    def test_unknown_model_returns_default(self):
        """Test that unknown models return default dimension."""
        dim = get_embedding_dimension("unknown-model")
        assert dim == 1536  # Default

    def test_default_model_constant(self):
        """Test that default model is set correctly."""
        assert DEFAULT_EMBEDDING_MODEL == "text-embedding-3-small"


class TestEmbeddingStability:
    """Tests for embedding text stability."""

    def test_same_pub_same_hash(self):
        """Test that same publication produces same content hash."""
        pub = {
            "title": "Cancer Study",
            "raw_text": "Abstract content",
            "summary": "",
            "source": "Nature",
            "venue": "Nature Cancer",
            "published_date": "2024-01-15",
        }

        text1 = build_embedding_text(pub)
        text2 = build_embedding_text(pub)

        hash1 = compute_content_hash(text1)
        hash2 = compute_content_hash(text2)

        assert hash1 == hash2

    def test_different_title_different_hash(self):
        """Test that changing title changes hash."""
        pub1 = {
            "title": "Original Title",
            "raw_text": "Abstract",
            "summary": "",
            "source": "Nature",
            "venue": "",
            "published_date": "",
        }

        pub2 = {
            "title": "Different Title",
            "raw_text": "Abstract",
            "summary": "",
            "source": "Nature",
            "venue": "",
            "published_date": "",
        }

        text1 = build_embedding_text(pub1)
        text2 = build_embedding_text(pub2)

        hash1 = compute_content_hash(text1)
        hash2 = compute_content_hash(text2)

        assert hash1 != hash2

    def test_different_abstract_different_hash(self):
        """Test that changing abstract changes hash."""
        pub1 = {
            "title": "Same Title",
            "raw_text": "Original abstract content",
            "summary": "",
            "source": "Nature",
            "venue": "",
            "published_date": "",
        }

        pub2 = {
            "title": "Same Title",
            "raw_text": "Different abstract content",
            "summary": "",
            "source": "Nature",
            "venue": "",
            "published_date": "",
        }

        text1 = build_embedding_text(pub1)
        text2 = build_embedding_text(pub2)

        hash1 = compute_content_hash(text1)
        hash2 = compute_content_hash(text2)

        assert hash1 != hash2
