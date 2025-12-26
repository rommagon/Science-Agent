"""Shared data types for acitrack."""

import hashlib
from dataclasses import dataclass


@dataclass
class Publication:
    """A publication record fetched from a source."""

    id: str
    title: str
    authors: list[str]
    source: str
    date: str  # ISO 8601 string
    url: str
    raw_text: str
    summary: str
    run_id: str
    venue: str = ""  # Journal/venue name (e.g., "bioRxiv", "medRxiv")
    source_names: list[str] = None  # All sources where this pub appeared (for cross-source deduping)


def compute_id(title: str, source: str, url: str) -> str:
    """Compute a deterministic ID for a publication using SHA256.

    Args:
        title: Publication title
        source: Source name
        url: Publication URL

    Returns:
        Hexadecimal SHA256 hash string
    """
    content = f"{title}|{source}|{url}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
