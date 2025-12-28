"""Fetch publications from configured sources."""

import logging
import re
import time
from datetime import datetime, timedelta
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


def _strip_html_tags(text: str) -> str:
    """Strip HTML tags from text safely.

    Args:
        text: Text potentially containing HTML tags

    Returns:
        Text with HTML tags removed
    """
    if not text:
        return ""
    # Remove HTML tags using regex
    clean_text = re.sub(r'<[^>]+>', '', text)
    # Decode common HTML entities
    clean_text = clean_text.replace('&nbsp;', ' ')
    clean_text = clean_text.replace('&amp;', '&')
    clean_text = clean_text.replace('&lt;', '<')
    clean_text = clean_text.replace('&gt;', '>')
    clean_text = clean_text.replace('&quot;', '"')
    clean_text = clean_text.replace('&#39;', "'")
    # Clean up extra whitespace
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    return clean_text


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
) -> tuple[list[Publication], int]:
    """Fetch publications from a single RSS source.

    Args:
        source: Source configuration dictionary
        since_date: Only fetch publications newer than this date
        run_id: Unique identifier for this run

    Returns:
        Tuple of (List of Publication objects, missing_date_count)
    """
    source_name = source.get("name", "Unknown")
    url = source.get("url")

    if not url:
        logger.error("Source '%s' has no URL configured", source_name)
        return [], 0

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
            return [], 0

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

            # Extract authors with fallbacks (check multiple fields)
            authors = []
            if "authors" in entry and entry.authors:
                # feedparser's authors is a list of dicts with 'name' key
                authors = [author.get("name", "") for author in entry.authors if author.get("name")]
            elif "author" in entry and entry.author:
                # Plain author string
                authors = [entry.author]
            elif hasattr(entry, 'dc_creator') and entry.dc_creator:
                # Dublin Core creator field (common in some RSS feeds)
                authors = [entry.dc_creator] if isinstance(entry.dc_creator, str) else entry.dc_creator
            elif "creator" in entry and entry.creator:
                # Generic creator field
                authors = [entry.creator] if isinstance(entry.creator, str) else entry.creator

            # Extract raw text (prefer summary, fallback to description) and strip HTML
            raw_text = ""
            if "summary" in entry and entry.summary:
                raw_text = _strip_html_tags(entry.summary)
            elif "description" in entry and entry.description:
                raw_text = _strip_html_tags(entry.description)

            # Detect venue from feed metadata or entry
            venue = ""
            # Try to extract venue from feed title
            feed_title = feed.feed.get("title", "")
            if "biorxiv" in feed_title.lower() or "biorxiv" in url.lower():
                venue = "bioRxiv"
            elif "medrxiv" in feed_title.lower() or "medrxiv" in url.lower():
                venue = "medRxiv"
            # Could also check entry.get("prism_publicationname") or similar fields if available

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
                venue=venue,
            )
            publications.append(publication)

        logger.info(
            "Source '%s': fetched %d entries, kept %d after date filter (%d missing dates)",
            source_name,
            fetched_count,
            len(publications),
            missing_date_count
        )
        return publications, missing_date_count

    except requests.exceptions.TooManyRedirects:
        logger.error(
            "Source '%s': too many redirects (>%d), giving up",
            source_name,
            MAX_REDIRECTS
        )
        return [], 0
    except requests.exceptions.Timeout:
        logger.error(
            "Source '%s': request timed out after %ds",
            source_name,
            REQUEST_TIMEOUT
        )
        return [], 0
    except requests.exceptions.RequestException as e:
        logger.error("Source '%s': HTTP request failed: %s", source_name, e)
        return [], 0
    except Exception as e:
        logger.error("Source '%s': unexpected error: %s", source_name, e)
        return [], 0


def _parse_pubmed_date(date_str: str) -> tuple[Optional[datetime], bool, bool]:
    """Parse PubMed date string to datetime object with best-effort handling.

    Args:
        date_str: Date string from PubMed (various formats)

    Returns:
        Tuple of (datetime object or None, missing_date_flag, low_confidence_flag)
        missing_date_flag is True if no date could be parsed
        low_confidence_flag is True if only year was available
    """
    if not date_str:
        return None, True, False

    # Month name to number mapping
    month_map = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'june': 6,
        'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12
    }

    # Season to month mapping (conservative: use first month of season)
    season_map = {
        'winter': 1, 'spring': 4, 'summer': 7, 'fall': 10, 'autumn': 10
    }

    try:
        # Try YYYY/MM/DD format
        if '/' in date_str:
            parts = date_str.split('/')
            if len(parts) == 3:
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                return datetime(year, month, day), False, False

        # Try structured parsing for various formats
        date_lower = date_str.lower().strip()

        # YYYY only (e.g., "2025") - Use Jan 1 and mark as low confidence
        if date_lower.isdigit() and len(date_lower) == 4:
            year = int(date_lower)
            logger.debug("Parsed year-only date '%s' as %d-01-01 (low confidence)", date_str, year)
            return datetime(year, 1, 1), False, True

        # YYYY Mon format (e.g., "2025 Nov")
        parts = date_lower.split()
        if len(parts) == 2 and parts[0].isdigit() and len(parts[0]) == 4:
            year = int(parts[0])
            month_str = parts[1].replace('-', ' ').split()[0]  # Handle "Nov-Dec" -> "Nov"

            # Check if it's a season
            if month_str in season_map:
                month = season_map[month_str]
                logger.debug("Parsed season date '%s' as %d-%02d-01", date_str, year, month)
                return datetime(year, month, 1), False, False

            # Check if it's a month name
            if month_str in month_map:
                month = month_map[month_str]
                logger.debug("Parsed year-month date '%s' as %d-%02d-01", date_str, year, month)
                return datetime(year, month, 1), False, False

        # YYYY Mon DD format (e.g., "2025 Nov 15")
        if len(parts) == 3 and parts[0].isdigit() and len(parts[0]) == 4:
            year = int(parts[0])
            month_str = parts[1]
            if month_str in month_map and parts[2].isdigit():
                month = month_map[month_str]
                day = int(parts[2])
                return datetime(year, month, day), False, False

        # Fallback: try dateutil parser
        try:
            from dateutil import parser as date_parser
            parsed = date_parser.parse(date_str)
            return parsed, False, False
        except Exception:
            pass

    except Exception as e:
        logger.debug("Failed to parse PubMed date '%s': %s", date_str, e)

    return None, True, False


def pick_best_pubmed_date(article: dict) -> tuple[Optional[datetime], str, str, bool]:
    """Pick the best available date from PubMed article record.

    Prefers dates in this order:
    1. ArticleDate (if available in pubdate field with specific date)
    2. PubMedPubDate fields (epublish, pubmed status - not available in ESummary)
    3. Journal PubDate (fallback)

    Args:
        article: PubMed article dictionary from ESummary

    Returns:
        Tuple of (datetime, date_source, date_raw, low_confidence)
        - datetime: Parsed date or None
        - date_source: Which field was used (e.g., "pubdate", "sortpubdate")
        - date_raw: Original raw date string(s)
        - low_confidence: True if only year was available
    """
    # Try pubdate first (most reliable from ESummary)
    pubdate_str = article.get("pubdate", "")
    if pubdate_str:
        parsed_date, missing, low_conf = _parse_pubmed_date(pubdate_str)
        if parsed_date and not missing:
            return parsed_date, "pubdate", pubdate_str, low_conf

    # Try sortpubdate as fallback (sortable date string)
    sortpubdate_str = article.get("sortpubdate", "")
    if sortpubdate_str:
        # sortpubdate is typically in YYYY/MM/DD HH:MM format
        parsed_date, missing, low_conf = _parse_pubmed_date(sortpubdate_str)
        if parsed_date and not missing:
            return parsed_date, "sortpubdate", sortpubdate_str, low_conf

    # Try epubdate (electronic publication date)
    epubdate_str = article.get("epubdate", "")
    if epubdate_str:
        parsed_date, missing, low_conf = _parse_pubmed_date(epubdate_str)
        if parsed_date and not missing:
            return parsed_date, "epubdate", epubdate_str, low_conf

    # No valid date found
    raw_dates = f"pubdate={pubdate_str}, sortpubdate={sortpubdate_str}, epubdate={epubdate_str}"
    return None, "none", raw_dates, False


def clamp_future_date(
    pub_date: Optional[datetime],
    date_source: str,
    pub_id: str,
    title: str,
    source_name: str,
    low_confidence: bool
) -> Optional[datetime]:
    """Clamp dates that are suspiciously in the future.

    If a date is more than 2 days in the future, it's likely a parsing error
    (e.g., year-only dates defaulting to Jan 1 of next year).

    Args:
        pub_date: Publication date to validate
        date_source: Which field the date came from
        pub_id: Publication ID for logging
        title: Publication title for logging
        source_name: Source name for logging
        low_confidence: Whether this is a low-confidence parse (year-only)

    Returns:
        Validated date or None if date is invalid
    """
    if not pub_date:
        return None

    # Calculate future threshold: now + 2 days
    now = datetime.now()
    future_threshold = now + timedelta(days=2)

    if pub_date > future_threshold:
        # Date is suspiciously in the future
        logger.warning(
            "Suspicious future date detected: source='%s', id='%s', title='%s', "
            "date='%s' (from %s, low_conf=%s), threshold='%s' -> Setting to None",
            source_name,
            pub_id[:16],
            title[:60],
            pub_date.isoformat(),
            date_source,
            low_confidence,
            future_threshold.strftime("%Y-%m-%d")
        )
        return None

    return pub_date


def _fetch_pubmed_source(
    source: dict, since_date: datetime, run_id: str
) -> tuple[list[Publication], int]:
    """Fetch publications from PubMed using E-utilities.

    Args:
        source: Source configuration dictionary
        since_date: Only fetch publications newer than this date
        run_id: Unique identifier for this run

    Returns:
        Tuple of (List of Publication objects, missing_date_count)
    """
    source_name = source.get("name", "Unknown")
    query = source.get("query")
    retmax = source.get("retmax", 200)

    if not query:
        logger.error("Source '%s' has no query configured", source_name)
        return [], 0

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
            return [], 0

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

            # Build URL (needed for ID computation)
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            pub_id = compute_id(title, source_name, url)

            # Extract and parse date using improved date selection
            pub_date, date_source, date_raw, low_confidence = pick_best_pubmed_date(article)

            # Apply sanity clamp to reject future dates
            pub_date_clamped = clamp_future_date(
                pub_date, date_source, pub_id, title, source_name, low_confidence
            )

            # Track missing dates
            if pub_date_clamped is None:
                missing_date_count += 1
                logger.debug(
                    "Source '%s': PMID %s has no valid date (raw='%s', source='%s')",
                    source_name,
                    pmid,
                    date_raw[:100],
                    date_source
                )

            # Apply date filter - skip items with missing dates OR dates before cutoff
            if pub_date_clamped and pub_date_clamped < since_date:
                continue

            # Extract journal and source info for improved raw_text
            source_info = article.get("source", "")
            fulljournalname = article.get("fulljournalname", "")
            authors_str = ", ".join(authors) if authors else "N/A"
            date_str = pub_date_clamped.strftime("%Y-%m-%d") if pub_date_clamped else "MISSING"

            # Build metadata block for downstream enrichment
            raw_text = f"Journal: {fulljournalname or source_info}\nAuthors: {authors_str}\nPubDate: {date_str}"

            # Create Publication object with debug fields
            publication = Publication(
                id=pub_id,
                title=title,
                authors=authors,
                source=source_name,
                date=pub_date_clamped.isoformat() if pub_date_clamped else "",
                url=url,
                raw_text=raw_text,
                summary="",  # Will be filled by summarization step
                run_id=run_id,
                date_raw=date_raw,
                date_source=date_source,
            )
            publications.append(publication)

        kept_count = len(publications)
        logger.info(
            "Source '%s': fetched %d articles, kept %d after date filter, missing_date_count: %d",
            source_name,
            fetched_count,
            kept_count,
            missing_date_count
        )
        return publications, missing_date_count

    except requests.exceptions.Timeout:
        logger.error(
            "Source '%s': PubMed request timed out after %ds",
            source_name,
            REQUEST_TIMEOUT
        )
        return [], 0
    except requests.exceptions.RequestException as e:
        logger.error("Source '%s': PubMed API request failed: %s", source_name, e)
        return [], 0
    except Exception as e:
        logger.error("Source '%s': unexpected error: %s", source_name, e)
        return [], 0


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
            publications, missing_dates = _fetch_rss_source(source, since_date, run_id)
            all_publications.extend(publications)
            source_stats.append({
                "name": source_name,
                "type": source_type,
                "url": source.get("url", ""),
                "kept": len(publications),
                "missing_date_count": missing_dates,
            })
        elif source_type == "pubmed":
            publications, missing_dates = _fetch_pubmed_source(source, since_date, run_id)
            all_publications.extend(publications)
            source_stats.append({
                "name": source_name,
                "type": source_type,
                "query": source.get("query", ""),
                "retmax": source.get("retmax", 200),
                "kept": len(publications),
                "missing_date_count": missing_dates,
            })
        else:
            logger.warning(
                "Source '%s' has unsupported type '%s', skipping", source_name, source_type
            )

    logger.info("Total publications fetched: %d", len(all_publications))
    return all_publications, source_stats
