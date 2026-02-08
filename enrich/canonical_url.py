"""Canonical URL resolution for publications.

This module provides logic to resolve canonical URLs for publications
based on available identifiers (DOI, PMID, URL patterns).
"""

import logging
import re
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

logger = logging.getLogger(__name__)


# DOI regex pattern - matches DOI in various formats
DOI_PATTERN = re.compile(
    r'(?:doi[:\s]*)?(?:https?://(?:dx\.)?doi\.org/)?'
    r'(10\.\d{4,}/[^\s\'"<>\[\]]+)',
    re.IGNORECASE
)

# PMID regex pattern
PMID_PATTERN = re.compile(
    r'(?:pmid[:\s]*|pubmed\.ncbi\.nlm\.nih\.gov/)(\d{7,8})',
    re.IGNORECASE
)

# bioRxiv/medRxiv DOI pattern
BIORXIV_DOI_PATTERN = re.compile(
    r'10\.1101/(\d{4}\.\d{2}\.\d{2}\.\d+)',
    re.IGNORECASE
)

# arXiv ID pattern
ARXIV_PATTERN = re.compile(
    r'(?:arxiv[:\s]*|arxiv\.org/abs/)(\d{4}\.\d{4,5}(?:v\d+)?)',
    re.IGNORECASE
)


def normalize_url(url: str) -> Optional[str]:
    """Normalize a URL by stripping whitespace, ensuring https, and removing tracking params.

    Args:
        url: Raw URL string

    Returns:
        Normalized URL or None if invalid
    """
    if not url:
        return None

    # Strip whitespace
    url = url.strip()

    if not url:
        return None

    # Parse URL
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    # Ensure scheme
    scheme = parsed.scheme.lower() if parsed.scheme else 'https'
    if scheme == 'http':
        scheme = 'https'
    elif scheme not in ('https', 'http'):
        return None

    # Skip if no host
    if not parsed.netloc:
        return None

    # Common tracking parameters to remove
    tracking_params = {
        'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
        'fbclid', 'gclid', 'ref', 'source', 'mc_cid', 'mc_eid', '_hsenc', '_hsmi',
    }

    # Parse and filter query parameters
    query_params = parse_qs(parsed.query, keep_blank_values=False)
    filtered_params = {
        k: v for k, v in query_params.items()
        if k.lower() not in tracking_params
    }

    # Rebuild query string
    new_query = urlencode(filtered_params, doseq=True) if filtered_params else ''

    # Rebuild URL
    normalized = urlunparse((
        scheme,
        parsed.netloc.lower(),
        parsed.path.rstrip('/') if parsed.path != '/' else '/',
        parsed.params,
        new_query,
        '',  # Remove fragment
    ))

    return normalized


def extract_doi(text: str) -> Optional[str]:
    """Extract and normalize DOI from text.

    Args:
        text: Text that may contain a DOI

    Returns:
        Normalized DOI (lowercase, trimmed) or None
    """
    if not text:
        return None

    match = DOI_PATTERN.search(text)
    if match:
        doi = match.group(1)
        # Normalize: lowercase and trim trailing punctuation
        doi = doi.lower().rstrip('.,;:)')
        return doi

    return None


def extract_pmid(text: str) -> Optional[str]:
    """Extract PMID from text or URL.

    Args:
        text: Text that may contain a PMID

    Returns:
        PMID string or None
    """
    if not text:
        return None

    match = PMID_PATTERN.search(text)
    if match:
        return match.group(1)

    return None


def extract_arxiv_id(text: str) -> Optional[str]:
    """Extract arXiv ID from text or URL.

    Args:
        text: Text that may contain an arXiv ID

    Returns:
        arXiv ID string or None
    """
    if not text:
        return None

    match = ARXIV_PATTERN.search(text)
    if match:
        return match.group(1)

    return None


def detect_source_type(url: str, source: str) -> Optional[str]:
    """Detect the source type from URL and source name.

    Args:
        url: Publication URL
        source: Source name

    Returns:
        Source type string (pubmed, biorxiv, medrxiv, arxiv, rss) or None
    """
    if not url and not source:
        return None

    combined = f"{url or ''} {source or ''}".lower()

    if 'pubmed.ncbi.nlm.nih.gov' in combined or 'pubmed' in combined:
        return 'pubmed'
    elif 'biorxiv' in combined:
        return 'biorxiv'
    elif 'medrxiv' in combined:
        return 'medrxiv'
    elif 'arxiv' in combined:
        return 'arxiv'
    elif 'nature.com' in combined:
        return 'nature'
    elif 'science.org' in combined or 'sciencemag.org' in combined:
        return 'science'
    elif 'cell.com' in combined:
        return 'cell'
    elif 'thelancet.com' in combined:
        return 'lancet'
    elif 'nejm.org' in combined:
        return 'nejm'
    elif 'jama' in combined:
        return 'jama'

    return 'rss'


def resolve_canonical_url(publication: Dict) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Resolve canonical URL for a publication using available identifiers.

    Priority order:
    1. If row already has a URL from RSS feed, normalize and keep it
    2. If DOI is present: canonical_url = "https://doi.org/<doi>"
    3. If PMID is present: canonical_url = "https://pubmed.ncbi.nlm.nih.gov/<pmid>/"
    4. If source is bioRxiv/medRxiv and DOI pattern matches, derive canonical URL
    5. If arXiv ID exists, derive canonical URL
    6. Return None if no canonical URL can be resolved

    Args:
        publication: Dictionary with publication data (id, title, url, doi, pmid, source, raw_text)

    Returns:
        Tuple of (canonical_url, doi, pmid, source_type)
    """
    url = publication.get('url', '')
    existing_doi = publication.get('doi', '')
    existing_pmid = publication.get('pmid', '')
    source = publication.get('source', '')
    raw_text = publication.get('raw_text', '')
    title = publication.get('title', '')

    # Combine text sources for identifier extraction
    search_text = f"{url} {existing_doi or ''} {existing_pmid or ''} {raw_text} {title}"

    # Extract identifiers
    doi = existing_doi or extract_doi(search_text)
    pmid = existing_pmid or extract_pmid(search_text)

    # Detect source type
    source_type = detect_source_type(url, source)

    # Try to extract PMID from PubMed URL
    if not pmid and url and 'pubmed.ncbi.nlm.nih.gov' in url:
        pmid = extract_pmid(url)

    # Priority 1: If DOI is present, use doi.org
    if doi:
        canonical_url = f"https://doi.org/{doi}"
        return normalize_url(canonical_url), doi, pmid, source_type

    # Priority 2: If PMID is present, use PubMed
    if pmid:
        canonical_url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        return normalize_url(canonical_url), doi, pmid, source_type

    # Priority 3: Check for arXiv
    arxiv_id = extract_arxiv_id(search_text)
    if arxiv_id:
        canonical_url = f"https://arxiv.org/abs/{arxiv_id}"
        return normalize_url(canonical_url), doi, pmid, source_type or 'arxiv'

    # Priority 4: Normalize existing URL if present
    if url:
        normalized = normalize_url(url)
        if normalized:
            return normalized, doi, pmid, source_type

    # No canonical URL could be resolved
    return None, doi, pmid, source_type


def extract_pmid_from_pubmed_url(url: str) -> Optional[str]:
    """Extract PMID from a PubMed URL.

    Args:
        url: PubMed URL

    Returns:
        PMID string or None
    """
    if not url or 'pubmed.ncbi.nlm.nih.gov' not in url:
        return None

    # Match patterns like /12345678/ or /12345678
    match = re.search(r'/(\d{7,8})/?(?:\?|$)', url)
    if match:
        return match.group(1)

    return None


def build_doi_url(doi: str) -> str:
    """Build a canonical DOI URL.

    Args:
        doi: DOI string (with or without prefix)

    Returns:
        Full DOI URL
    """
    # Remove any existing prefix
    doi = doi.strip()
    if doi.lower().startswith('https://doi.org/'):
        doi = doi[16:]
    elif doi.lower().startswith('http://doi.org/'):
        doi = doi[15:]
    elif doi.lower().startswith('doi:'):
        doi = doi[4:]

    # Normalize to lowercase
    doi = doi.lower().strip()

    return f"https://doi.org/{doi}"


def build_pubmed_url(pmid: str) -> str:
    """Build a canonical PubMed URL.

    Args:
        pmid: PMID string

    Returns:
        Full PubMed URL
    """
    return f"https://pubmed.ncbi.nlm.nih.gov/{pmid.strip()}/"
