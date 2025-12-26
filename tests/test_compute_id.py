"""Test compute_id function for deterministic ID generation."""

from acitrack_types import compute_id


def test_compute_id_deterministic():
    """Test that compute_id generates same ID for same inputs."""
    title = "Novel cancer therapy shows promise"
    source = "Nature Cancer"
    url = "https://example.com/article"

    # Call twice with same inputs
    id1 = compute_id(title, source, url)
    id2 = compute_id(title, source, url)

    # Should be identical
    assert id1 == id2


def test_compute_id_different_titles():
    """Test that different titles produce different IDs."""
    source = "Nature Cancer"
    url = "https://example.com/article"

    id1 = compute_id("Title A", source, url)
    id2 = compute_id("Title B", source, url)

    assert id1 != id2


def test_compute_id_different_sources():
    """Test that different sources produce different IDs."""
    title = "Novel cancer therapy"
    url = "https://example.com/article"

    id1 = compute_id(title, "Nature Cancer", url)
    id2 = compute_id(title, "Science", url)

    assert id1 != id2


def test_compute_id_different_urls():
    """Test that different URLs produce different IDs."""
    title = "Novel cancer therapy"
    source = "Nature Cancer"

    id1 = compute_id(title, source, "https://example.com/article1")
    id2 = compute_id(title, source, "https://example.com/article2")

    assert id1 != id2


def test_compute_id_format():
    """Test that ID is a valid SHA256 hex string."""
    id = compute_id("Test", "Source", "https://url.com")

    # SHA256 produces 64 hex characters
    assert len(id) == 64
    assert all(c in "0123456789abcdef" for c in id)
