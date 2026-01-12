"""Cross-source deduplication for publications."""

import logging
import re
from typing import Optional

from acitrack_types import Publication

logger = logging.getLogger(__name__)


def normalize_title(title: str) -> str:
    """Normalize title for deduplication matching.

    Args:
        title: Raw publication title

    Returns:
        Normalized title (lowercase, no punctuation/whitespace)
    """
    if not title:
        return ""

    # Lowercase and remove punctuation/extra whitespace
    normalized = title.lower()
    normalized = re.sub(r'[^\w\s]', '', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


def extract_doi(pub: Publication) -> Optional[str]:
    """Extract DOI from publication URL or text.

    Args:
        pub: Publication object

    Returns:
        DOI string or None
    """
    # Search in URL first
    if pub.url:
        doi_match = re.search(r'10\.\d{4,}/[^\s]+', pub.url, re.IGNORECASE)
        if doi_match:
            doi = doi_match.group(0).rstrip('.,;)')
            return doi.lower()

    # Search in title + raw_text
    combined_text = pub.title + " " + pub.raw_text
    doi_match = re.search(r'10\.\d{4,}/[^\s]+', combined_text, re.IGNORECASE)
    if doi_match:
        doi = doi_match.group(0).rstrip('.,;)')
        return doi.lower()

    return None


def extract_pmid(pub: Publication) -> Optional[str]:
    """Extract PMID from publication URL if present.

    Args:
        pub: Publication object

    Returns:
        PMID string or None
    """
    if not pub.url:
        return None

    # Match patterns like:
    # - https://pubmed.ncbi.nlm.nih.gov/12345678
    # - https://pubmed.ncbi.nlm.nih.gov/12345678/
    # - https://www.ncbi.nlm.nih.gov/pubmed/12345678
    # - PMID: 12345678
    pmid_match = re.search(r'pubmed.*?/(\d+)', pub.url, re.IGNORECASE)
    if pmid_match:
        return pmid_match.group(1)

    pmid_match = re.search(r'PMID:?\s*(\d+)', pub.url, re.IGNORECASE)
    if pmid_match:
        return pmid_match.group(1)

    return None


def extract_first_author(pub: Publication) -> Optional[str]:
    """Extract first author from publication.

    Args:
        pub: Publication object

    Returns:
        First author name (normalized) or None
    """
    if not pub.authors or len(pub.authors) == 0:
        return None

    # Normalize first author (lowercase, no punctuation)
    first_author = pub.authors[0].lower()
    first_author = re.sub(r'[^\w\s]', '', first_author)
    first_author = re.sub(r'\s+', ' ', first_author).strip()

    return first_author if first_author else None


def extract_year(pub: Publication) -> Optional[str]:
    """Extract publication year from date field.

    Args:
        pub: Publication object

    Returns:
        Year string (YYYY) or None
    """
    if not pub.date:
        return None

    # Extract year from ISO date string (YYYY-MM-DD...)
    year_match = re.search(r'(\d{4})', pub.date)
    if year_match:
        return year_match.group(1)

    return None


def get_dedupe_key(pub: Publication) -> str:
    """Generate deduplication key for a publication.

    STRICT PRECEDENCE (as per requirements):
    1. DOI (most reliable)
    2. PMID (PubMed-specific)
    3. title + first_author + year hash (for non-indexed papers)
    4. URL (fallback)
    5. normalized title (last resort)

    Args:
        pub: Publication object

    Returns:
        Deduplication key string
    """
    # 1. Prefer DOI if available (HIGHEST PRIORITY)
    doi = extract_doi(pub)
    if doi:
        return f"doi:{doi}"

    # 2. PMID if available
    pmid = extract_pmid(pub)
    if pmid:
        return f"pmid:{pmid}"

    # 3. Title + first_author + year composite (for non-indexed papers)
    first_author = extract_first_author(pub)
    year = extract_year(pub)
    norm_title = normalize_title(pub.title)

    if norm_title and first_author and year:
        # Create composite key: hash of title + author + year
        composite = f"{norm_title}|{first_author}|{year}"
        return f"composite:{composite}"

    # 4. Use URL if available and looks stable
    if pub.url and not pub.url.endswith('#') and len(pub.url) > 10:
        return f"url:{pub.url.lower().strip()}"

    # 5. Fallback to normalized title (last resort)
    return f"title:{norm_title}"


def deduplicate_publications(publications: list[Publication]) -> tuple[list[Publication], dict]:
    """Deduplicate publications across sources.

    When duplicates are found:
    - Keep ONE canonical publication record
    - Merge sources into source_names list
    - Primary source is the first encountered

    Args:
        publications: List of publications (may contain duplicates)

    Returns:
        Tuple of (deduped_publications, stats_dict)
        stats_dict contains:
            - total_input: Original count
            - total_output: Deduped count
            - duplicates_merged: Number of duplicates removed
    """
    logger.info("Deduplicating %d publications across sources", len(publications))

    # Track publications by dedupe key
    seen = {}
    deduped = []
    duplicates_count = 0

    for pub in publications:
        dedupe_key = get_dedupe_key(pub)

        if dedupe_key in seen:
            # Duplicate found - merge sources
            existing_pub = seen[dedupe_key]

            # Add current source to source_names if not already there
            if not hasattr(existing_pub, 'source_names'):
                existing_pub.source_names = [existing_pub.source]

            if pub.source not in existing_pub.source_names:
                existing_pub.source_names.append(pub.source)

            duplicates_count += 1
            logger.debug(
                "Duplicate found: '%s' (sources: %s)",
                pub.title[:60],
                existing_pub.source_names
            )
        else:
            # New publication
            # Initialize source_names with current source
            pub.source_names = [pub.source]
            seen[dedupe_key] = pub
            deduped.append(pub)

    stats = {
        "total_input": len(publications),
        "total_output": len(deduped),
        "duplicates_merged": duplicates_count,
    }

    logger.info(
        "Deduplication complete: %d → %d publications (%d duplicates merged)",
        stats["total_input"],
        stats["total_output"],
        stats["duplicates_merged"]
    )

    return deduped, stats
