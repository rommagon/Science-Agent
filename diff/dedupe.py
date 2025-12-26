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


def extract_pmid(pub: Publication) -> Optional[str]:
    """Extract PMID from publication URL if present.

    Args:
        pub: Publication object

    Returns:
        PMID string or None
    """
    if not pub.url:
        return None

    # Match patterns like: pubmed/12345678 or PMID: 12345678
    pmid_match = re.search(r'pubmed/(\d+)', pub.url, re.IGNORECASE)
    if pmid_match:
        return pmid_match.group(1)

    pmid_match = re.search(r'PMID:?\s*(\d+)', pub.url, re.IGNORECASE)
    if pmid_match:
        return pmid_match.group(1)

    return None


def get_dedupe_key(pub: Publication) -> str:
    """Generate deduplication key for a publication.

    Prefer PMID > URL > normalized title

    Args:
        pub: Publication object

    Returns:
        Deduplication key string
    """
    # Prefer PMID if available
    pmid = extract_pmid(pub)
    if pmid:
        return f"pmid:{pmid}"

    # Use URL if available and looks stable
    if pub.url and not pub.url.endswith('#') and len(pub.url) > 10:
        return f"url:{pub.url.lower().strip()}"

    # Fallback to normalized title
    norm_title = normalize_title(pub.title)
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
