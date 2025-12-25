"""Fetch publications from configured sources."""

import logging
from datetime import datetime

from acitrack_types import Publication

logger = logging.getLogger(__name__)


def fetch_publications(
    sources: list[dict], since_date: datetime, run_id: str, outdir: str
) -> list[Publication]:
    """Fetch publications from all configured sources.

    Args:
        sources: List of source configurations from sources.yaml
        since_date: Only fetch publications newer than this date
        run_id: Unique identifier for this run
        outdir: Output directory for data

    Returns:
        List of Publication objects
    """
    logger.info(
        "Fetching publications from %d sources since %s",
        len(sources),
        since_date.isoformat(),
    )
    logger.warning("Ingestion not implemented - returning empty list")
    return []
