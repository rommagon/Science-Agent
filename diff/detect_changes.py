"""Detect changes between publication snapshots."""

import logging

from acitrack_types import Publication

logger = logging.getLogger(__name__)


def detect_changes(
    publications: list[Publication], snapshot_dir: str
) -> dict[str, list[Publication]]:
    """Detect new and updated publications by comparing with previous snapshots.

    Args:
        publications: Current batch of publications
        snapshot_dir: Directory containing previous snapshots

    Returns:
        Dictionary with keys 'new' and 'updated', each containing a list of publications
    """
    logger.info("Detecting changes in %d publications", len(publications))
    logger.warning("Change detection not implemented")
    return {"new": [], "updated": []}
