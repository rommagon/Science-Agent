"""Generate summaries for publications."""

import logging

from acitrack_types import Publication

logger = logging.getLogger(__name__)


def summarize_publications(publications: list[Publication]) -> list[Publication]:
    """Generate summaries for publications.

    Args:
        publications: List of publications to summarize

    Returns:
        List of publications with summary field populated
    """
    logger.info("Summarizing %d publications", len(publications))
    logger.warning("Summarization not implemented")
    return publications
