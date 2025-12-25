"""Fetch publications from configured sources."""

import logging
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Optional

import feedparser

from acitrack_types import Publication, compute_id

logger = logging.getLogger(__name__)


def _parse_rss_date(date_str: str) -> Optional[datetime]:
    """Parse RSS date string to datetime object.

    Args:
        date_str: Date string from RSS feed

    Returns:
        datetime object in UTC, or None if parsing fails
    """
    if not date_str:
        return None

    try:
        # Try ISO 8601 format first (common in modern feeds)
        if 'T' in date_str and (date_str.endswith('Z') or '+' in date_str or date_str.count('-') > 2):
            # Remove 'Z' suffix and parse as ISO format
            date_str_clean = date_str.replace('Z', '+00:00')
            dt = datetime.fromisoformat(date_str_clean)
            # Convert to naive UTC
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt

        # Fall back to email date parsing (RFC 2822)
        dt = parsedate_to_datetime(date_str)
        if dt is None:
            return None
        # Convert to naive datetime
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except Exception as e:
        logger.warning("Failed to parse date '%s': %s", date_str, e)
        return None


def _fetch_rss_source(
    source: dict, since_date: datetime, run_id: str
) -> list[Publication]:
    """Fetch publications from a single RSS source.

    Args:
        source: Source configuration dictionary
        since_date: Only fetch publications newer than this date
        run_id: Unique identifier for this run

    Returns:
        List of Publication objects from this source
    """
    source_name = source.get("name", "Unknown")
    url = source.get("url")

    if not url:
        logger.error("Source '%s' has no URL configured", source_name)
        return []

    try:
        logger.info("Fetching RSS feed: %s", source_name)
        feed = feedparser.parse(url)

        if feed.bozo and not feed.entries:
            logger.error(
                "Failed to parse RSS feed '%s': %s", source_name, feed.get("bozo_exception", "Unknown error")
            )
            return []

        publications = []
        fetched_count = 0

        for entry in feed.entries:
            fetched_count += 1

            # Extract publication date
            pub_date = None
            date_str = entry.get("published") or entry.get("updated")
            if date_str:
                pub_date = _parse_rss_date(date_str)

            # Filter by date if available
            if pub_date and pub_date < since_date:
                continue

            # Extract title and URL
            title = entry.get("title", "Untitled")
            entry_url = entry.get("link", "")

            # Extract authors
            authors = []
            if "authors" in entry:
                authors = [author.get("name", "") for author in entry.authors if author.get("name")]
            elif "author" in entry:
                authors = [entry.author]

            # Extract raw text (prefer summary, fallback to description)
            raw_text = ""
            if "summary" in entry:
                raw_text = entry.summary
            elif "description" in entry:
                raw_text = entry.description

            # Create Publication object
            pub_id = compute_id(title, source_name, entry_url)
            publication = Publication(
                id=pub_id,
                title=title,
                authors=authors,
                source=source_name,
                date=pub_date.isoformat() if pub_date else "",
                url=entry_url,
                raw_text=raw_text,
                summary="",  # Will be filled by summarization step
                run_id=run_id,
            )
            publications.append(publication)

        logger.info(
            "Source '%s': fetched %d entries, kept %d after date filter",
            source_name,
            fetched_count,
            len(publications),
        )
        return publications

    except Exception as e:
        logger.error("Error fetching RSS feed '%s': %s", source_name, e)
        return []


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

    all_publications = []

    for source in sources:
        source_type = source.get("type", "").lower()
        source_name = source.get("name", "Unknown")

        if source_type == "rss":
            publications = _fetch_rss_source(source, since_date, run_id)
            all_publications.extend(publications)
        else:
            logger.warning(
                "Source '%s' has unsupported type '%s', skipping", source_name, source_type
            )

    logger.info("Total publications fetched: %d", len(all_publications))
    return all_publications
