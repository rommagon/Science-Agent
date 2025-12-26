"""Fetch publications from configured sources."""

import logging
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Optional

import feedparser
import requests

from acitrack_types import Publication, compute_id

logger = logging.getLogger(__name__)

# User agent for RSS fetching
USER_AGENT = "acitrack-v1/0.1 (+https://github.com/spotitearly/acitrack)"
REQUEST_TIMEOUT = 15  # seconds
MAX_REDIRECTS = 5

# PubMed E-utilities configuration
PUBMED_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
PUBMED_POLITENESS_DELAY = 0.34  # seconds between API calls


def _parse_rss_date(date_str: str = None, time_struct: time.struct_time = None) -> Optional[datetime]:
    """Parse RSS date from string or time_struct.

    Args:
        date_str: Date string from RSS feed (optional)
        time_struct: Parsed time struct from feedparser (optional)

    Returns:
        datetime object in UTC, or None if parsing fails
    """
    # Try time_struct first (from feedparser's published_parsed or updated_parsed)
    if time_struct:
        try:
            dt = datetime(*time_struct[:6])
            return dt
        except Exception as e:
            logger.debug("Failed to parse time_struct: %s", e)

    # Fall back to date string parsing
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
        logger.debug("Failed to parse date '%s': %s", date_str, e)
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
        logger.info("Fetching RSS feed: %s from %s", source_name, url)

        # Fetch RSS feed using requests with proper headers and redirect handling
        session = requests.Session()
        session.max_redirects = MAX_REDIRECTS

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/xml, */*"
        }

        response = session.get(
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )

        # Log HTTP response details
        final_url = response.url
        if final_url != url:
            logger.info("Source '%s': redirected to %s", source_name, final_url)
        logger.info("Source '%s': HTTP %d", source_name, response.status_code)

        response.raise_for_status()

        # Parse feed from content
        feed = feedparser.parse(response.content)

        if feed.bozo and not feed.entries:
            error_msg = str(feed.get("bozo_exception", "Unknown error"))
            logger.error("Failed to parse RSS feed '%s': %s", source_name, error_msg)
            return []

        publications = []
        fetched_count = 0
        missing_date_count = 0

        for entry in feed.entries:
            fetched_count += 1

            # Extract publication date - try parsed time_struct first, then string
            pub_date = None
            time_struct = entry.get("published_parsed") or entry.get("updated_parsed")
            date_str = entry.get("published") or entry.get("updated")

            pub_date = _parse_rss_date(date_str=date_str, time_struct=time_struct)

            # If no date available, use current time and log warning
            if not pub_date:
                pub_date = datetime.now()
                missing_date_count += 1
                logger.warning(
                    "Source '%s': entry '%s' has no date, using current time",
                    source_name,
                    entry.get("title", "Untitled")[:50]
                )

            # Filter by date
            if pub_date < since_date:
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
                date=pub_date.isoformat(),
                url=entry_url,
                raw_text=raw_text,
                summary="",  # Will be filled by summarization step
                run_id=run_id,
            )
            publications.append(publication)

        logger.info(
            "Source '%s': fetched %d entries, kept %d after date filter (%d missing dates)",
            source_name,
            fetched_count,
            len(publications),
            missing_date_count
        )
        return publications

    except requests.exceptions.TooManyRedirects:
        logger.error(
            "Source '%s': too many redirects (>%d), giving up",
            source_name,
            MAX_REDIRECTS
        )
        return []
    except requests.exceptions.Timeout:
        logger.error(
            "Source '%s': request timed out after %ds",
            source_name,
            REQUEST_TIMEOUT
        )
        return []
    except requests.exceptions.RequestException as e:
        logger.error("Source '%s': HTTP request failed: %s", source_name, e)
        return []
    except Exception as e:
        logger.error("Source '%s': unexpected error: %s", source_name, e)
        return []


def _parse_pubmed_date(date_str: str) -> Optional[datetime]:
    """Parse PubMed date string to datetime object.

    Args:
        date_str: Date string from PubMed (format: YYYY/MM/DD or YYYY Mon DD)

    Returns:
        datetime object or None if parsing fails
    """
    if not date_str:
        return None

    try:
        # Try YYYY/MM/DD format
        if '/' in date_str:
            parts = date_str.split('/')
            if len(parts) == 3:
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                return datetime(year, month, day)

        # Try YYYY Mon DD format
        if ' ' in date_str:
            from dateutil import parser
            return parser.parse(date_str)

    except Exception as e:
        logger.debug("Failed to parse PubMed date '%s': %s", date_str, e)

    return None


def _fetch_pubmed_source(
    source: dict, since_date: datetime, run_id: str
) -> list[Publication]:
    """Fetch publications from PubMed using E-utilities.

    Args:
        source: Source configuration dictionary
        since_date: Only fetch publications newer than this date
        run_id: Unique identifier for this run

    Returns:
        List of Publication objects from this source
    """
    source_name = source.get("name", "Unknown")
    query = source.get("query")
    retmax = source.get("retmax", 200)

    if not query:
        logger.error("Source '%s' has no query configured", source_name)
        return []

    try:
        logger.info("Fetching PubMed publications: %s (query: '%s', retmax: %d)",
                    source_name, query, retmax)

        # Format dates for PubMed
        mindate = since_date.strftime("%Y/%m/%d")
        maxdate = datetime.now().strftime("%Y/%m/%d")

        # Step 1: ESearch to get PMIDs
        esearch_params = {
            "db": "pubmed",
            "term": query,
            "retmax": retmax,
            "retstart": 0,
            "sort": "date",
            "datetype": "pdat",
            "mindate": mindate,
            "maxdate": maxdate,
            "retmode": "json"
        }

        logger.info("Source '%s': searching PubMed from %s to %s", source_name, mindate, maxdate)

        response = requests.get(
            PUBMED_ESEARCH_URL,
            params=esearch_params,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()

        search_result = response.json()
        pmids = search_result.get("esearchresult", {}).get("idlist", [])

        if not pmids:
            logger.info("Source '%s': no PMIDs found", source_name)
            return []

        logger.info("Source '%s': found %d PMIDs", source_name, len(pmids))

        # Be polite to NCBI
        time.sleep(PUBMED_POLITENESS_DELAY)

        # Step 2: ESummary to get publication details
        esummary_params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "json"
        }

        response = requests.get(
            PUBMED_ESUMMARY_URL,
            params=esummary_params,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()

        summary_result = response.json()
        results = summary_result.get("result", {})

        publications = []
        fetched_count = 0
        missing_date_count = 0

        for pmid in pmids:
            if pmid not in results or pmid == "uids":
                continue

            article = results[pmid]
            fetched_count += 1

            # Extract title
            title = article.get("title", "Untitled")
            if title.endswith("."):
                title = title[:-1]  # Remove trailing period

            # Extract authors
            authors = []
            author_list = article.get("authors", [])
            for author in author_list:
                if isinstance(author, dict):
                    name = author.get("name", "")
                    if name:
                        authors.append(name)

            # Extract and parse date
            pub_date = None
            pubdate_str = article.get("pubdate", "")
            if pubdate_str:
                pub_date = _parse_pubmed_date(pubdate_str)

            # If no date available, use current time and log warning
            if not pub_date:
                pub_date = datetime.now()
                missing_date_count += 1
                logger.warning(
                    "Source '%s': PMID %s has no parseable date, using current time",
                    source_name,
                    pmid
                )

            # Apply date filter
            if pub_date < since_date:
                continue

            # Build URL
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

            # Extract journal and source info for raw_text
            source_info = article.get("source", "")
            fulljournalname = article.get("fulljournalname", "")
            raw_text = f"Journal: {fulljournalname or source_info}"

            # Create Publication object
            pub_id = compute_id(title, source_name, url)
            publication = Publication(
                id=pub_id,
                title=title,
                authors=authors,
                source=source_name,
                date=pub_date.isoformat(),
                url=url,
                raw_text=raw_text,
                summary="",  # Will be filled by summarization step
                run_id=run_id,
            )
            publications.append(publication)

        logger.info(
            "Source '%s': fetched %d articles, kept %d after date filter (%d missing dates)",
            source_name,
            fetched_count,
            len(publications),
            missing_date_count
        )
        return publications

    except requests.exceptions.Timeout:
        logger.error(
            "Source '%s': PubMed request timed out after %ds",
            source_name,
            REQUEST_TIMEOUT
        )
        return []
    except requests.exceptions.RequestException as e:
        logger.error("Source '%s': PubMed API request failed: %s", source_name, e)
        return []
    except Exception as e:
        logger.error("Source '%s': unexpected error: %s", source_name, e)
        return []


def fetch_publications(
    sources: list[dict], since_date: datetime, run_id: str, outdir: str
) -> tuple[list[Publication], list[dict]]:
    """Fetch publications from all configured sources.

    Args:
        sources: List of source configurations from sources.yaml
        since_date: Only fetch publications newer than this date
        run_id: Unique identifier for this run
        outdir: Output directory for data

    Returns:
        Tuple of (List of Publication objects, List of per-source statistics)
    """
    logger.info(
        "Fetching publications from %d sources since %s",
        len(sources),
        since_date.isoformat(),
    )

    all_publications = []
    source_stats = []

    for source in sources:
        source_type = source.get("type", "").lower()
        source_name = source.get("name", "Unknown")

        if source_type == "rss":
            publications = _fetch_rss_source(source, since_date, run_id)
            all_publications.extend(publications)
            source_stats.append({
                "name": source_name,
                "type": source_type,
                "url": source.get("url", ""),
                "kept": len(publications),
            })
        elif source_type == "pubmed":
            publications = _fetch_pubmed_source(source, since_date, run_id)
            all_publications.extend(publications)
            source_stats.append({
                "name": source_name,
                "type": source_type,
                "query": source.get("query", ""),
                "retmax": source.get("retmax", 200),
                "kept": len(publications),
            })
        else:
            logger.warning(
                "Source '%s' has unsupported type '%s', skipping", source_name, source_type
            )

    logger.info("Total publications fetched: %d", len(all_publications))
    return all_publications, source_stats
