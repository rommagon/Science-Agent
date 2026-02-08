#!/usr/bin/env python3
"""Score seed papers using the tri-model scoring pipeline.

This script takes a list of seed papers (URLs/DOIs/PMIDs) and runs the existing
tri-model scoring pipeline on them, writing standard artifacts that can be
ingested by the backend.

Usage:
    python scripts/score_seed_papers.py --input seeds.json

    # With backend ingestion
    python scripts/score_seed_papers.py --input seeds.json --ingest-backend

    # Custom run ID and output directory
    python scripts/score_seed_papers.py --input seeds.json \
        --run-id tri-model-seeds-2026-01-25 \
        --output-dir data/outputs/tri-model-seeds/custom-run

Input format (seeds.json):
    [
        {"type": "url", "value": "https://www.nature.com/articles/s41586-024-07051-0"},
        {"type": "doi", "value": "10.1038/s41586-024-07051-0"},
        {"type": "pmid", "value": "39385123"}
    ]

Output:
    - manifest.json: Run metadata compatible with POST /ingest/run
    - tri_model_events.jsonl: Scoring events compatible with POST /ingest/tri-model-events

Environment variables:
    - CLAUDE_API_KEY: Required for Claude reviewer
    - GEMINI_API_KEY: Required for Gemini reviewer
    - SPOTITEARLY_LLM_API_KEY: Required for GPT evaluator and credibility scoring
    - TRI_MODEL_MINI_DAILY: Must be set to 'true' to enable tri-model
    - BACKEND_URL: Backend URL for ingestion (optional)
    - BACKEND_API_KEY: Backend API key for ingestion (optional)
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

# Try to import httpx for better HTML fetching (falls back to requests)
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Version for this seed scoring script
SEED_SCORING_VERSION = "v1"


def _get_prompt_metadata() -> Dict[str, str]:
    from config.tri_model_config import TRI_MODEL_PROMPT_VERSION, RELEVANCY_RUBRIC_VERSION
    from tri_model.prompts import get_prompt_hashes

    prompt_version = TRI_MODEL_PROMPT_VERSION
    prompt_hashes = get_prompt_hashes(prompt_version)
    return {
        "prompt_version": prompt_version,
        "rubric_version": RELEVANCY_RUBRIC_VERSION,
        "prompt_hash": prompt_hashes["combined"],
        "prompt_hashes": prompt_hashes,
    }

# Patterns for extracting DOI/PMID from URLs
DOI_URL_PATTERNS = [
    r"doi\.org/(10\.\d{4,}/[^\s?#]+)",
    r"dx\.doi\.org/(10\.\d{4,}/[^\s?#]+)",
    r"nature\.com/articles/(10\.\d{4,}/[^\s?#]+)",
    r"science\.org/doi/(10\.\d{4,}/[^\s?#]+)",
    r"cell\.com/.*/(10\.\d{4,}/[^\s?#]+)",
    r"nejm\.org/doi/full/(10\.\d{4,}/[^\s?#]+)",
    r"thelancet\.com/.*/(10\.\d{4,}/[^\s?#]+)",
    r"plos\.org/.*/(10\.\d{4,}/[^\s?#]+)",
    r"biorxiv\.org/.*/(10\.\d{4,}/[^\s?#]+)",
    r"medrxiv\.org/.*/(10\.\d{4,}/[^\s?#]+)",
]

PMID_URL_PATTERNS = [
    r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)",
    r"ncbi\.nlm\.nih\.gov/pubmed/(\d+)",
]

# Browser-like User-Agent for fetching publisher pages
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Global force-DOI map (populated from --force-doi-map)
_FORCE_DOI_MAP: Dict[str, str] = {}


def load_force_doi_map(map_path: str) -> Dict[str, str]:
    """Load URL->DOI mappings from JSON file.

    Args:
        map_path: Path to JSON file with {url: doi} mappings

    Returns:
        Dictionary mapping URLs to DOIs
    """
    global _FORCE_DOI_MAP
    try:
        with open(map_path, "r", encoding="utf-8") as f:
            _FORCE_DOI_MAP = json.load(f)
        logger.info("Loaded %d force-DOI mappings from %s", len(_FORCE_DOI_MAP), map_path)
        return _FORCE_DOI_MAP
    except Exception as e:
        logger.warning("Failed to load force-DOI map from %s: %s", map_path, e)
        return {}


def extract_doi_from_url(url: str) -> Optional[str]:
    """Extract DOI from a URL.

    Args:
        url: URL that may contain a DOI

    Returns:
        DOI string or None
    """
    for pattern in DOI_URL_PATTERNS:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            doi = match.group(1)
            # Clean up DOI (remove trailing punctuation)
            doi = re.sub(r"[.,;:]+$", "", doi)
            return doi
    return None


def extract_pmid_from_url(url: str) -> Optional[str]:
    """Extract PMID from a URL.

    Args:
        url: URL that may contain a PMID

    Returns:
        PMID string or None
    """
    for pattern in PMID_URL_PATTERNS:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def fetch_html_with_browser_ua(url: str, timeout: int = 30) -> Optional[str]:
    """Fetch HTML content from a URL using browser-like User-Agent.

    Args:
        url: URL to fetch
        timeout: Request timeout in seconds

    Returns:
        HTML content as string, or None on error
    """
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        if HTTPX_AVAILABLE:
            with httpx.Client(follow_redirects=True, timeout=timeout) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                return response.text
        else:
            response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            return response.text
    except Exception as e:
        logger.warning("Failed to fetch HTML from %s: %s", url, e)
        return None


def extract_doi_from_html(html: str) -> Optional[str]:
    """Extract DOI from HTML page content.

    Looks for DOI in:
    - meta[name="citation_doi"]
    - meta[name="dc.identifier"] containing "doi:"
    - meta[property="og:url"] containing doi.org
    - Any href containing "https://doi.org/"
    - JSON-LD script tags

    Args:
        html: HTML content as string

    Returns:
        DOI string or None
    """
    if not html:
        return None

    # Pattern to match DOI (10.XXXX/...)
    doi_pattern = r"10\.\d{4,}/[^\s\"'<>)}\]]+[^\s\"'<>)}\].,;:]"

    # 1. Check meta[name="citation_doi"]
    match = re.search(
        r'<meta\s+[^>]*name\s*=\s*["\']citation_doi["\']\s+[^>]*content\s*=\s*["\']([^"\']+)["\']',
        html,
        re.IGNORECASE
    )
    if not match:
        match = re.search(
            r'<meta\s+[^>]*content\s*=\s*["\']([^"\']+)["\']\s+[^>]*name\s*=\s*["\']citation_doi["\']',
            html,
            re.IGNORECASE
        )
    if match:
        doi_candidate = match.group(1).strip()
        # Extract DOI from the content (may be full URL or just DOI)
        doi_match = re.search(doi_pattern, doi_candidate)
        if doi_match:
            doi = doi_match.group(0).rstrip(".,;:")
            logger.debug("Found DOI in citation_doi meta tag: %s", doi)
            return doi

    # 2. Check meta[name="dc.identifier"] with "doi:" prefix
    for match in re.finditer(
        r'<meta\s+[^>]*name\s*=\s*["\']dc\.identifier["\']\s+[^>]*content\s*=\s*["\']([^"\']+)["\']',
        html,
        re.IGNORECASE
    ):
        content = match.group(1).strip()
        if "doi:" in content.lower() or "10." in content:
            doi_match = re.search(doi_pattern, content)
            if doi_match:
                doi = doi_match.group(0).rstrip(".,;:")
                logger.debug("Found DOI in dc.identifier meta tag: %s", doi)
                return doi

    # 3. Check meta[property="og:url"] for doi.org link
    match = re.search(
        r'<meta\s+[^>]*property\s*=\s*["\']og:url["\']\s+[^>]*content\s*=\s*["\']([^"\']+)["\']',
        html,
        re.IGNORECASE
    )
    if match:
        og_url = match.group(1).strip()
        if "doi.org" in og_url:
            doi_match = re.search(doi_pattern, og_url)
            if doi_match:
                doi = doi_match.group(0).rstrip(".,;:")
                logger.debug("Found DOI in og:url meta tag: %s", doi)
                return doi

    # 4. Check for href="https://doi.org/..." links
    for match in re.finditer(r'href\s*=\s*["\']https?://(?:dx\.)?doi\.org/([^"\']+)["\']', html, re.IGNORECASE):
        doi_candidate = match.group(1).strip()
        doi_match = re.search(doi_pattern, "10." + doi_candidate if not doi_candidate.startswith("10.") else doi_candidate)
        if doi_match:
            doi = doi_match.group(0).rstrip(".,;:")
            logger.debug("Found DOI in doi.org href: %s", doi)
            return doi

    # 5. Check JSON-LD script tags
    for match in re.finditer(r'<script\s+[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.IGNORECASE | re.DOTALL):
        try:
            json_content = match.group(1)
            data = json.loads(json_content)

            # Handle array of objects
            if isinstance(data, list):
                for item in data:
                    doi = _extract_doi_from_jsonld(item)
                    if doi:
                        return doi
            else:
                doi = _extract_doi_from_jsonld(data)
                if doi:
                    return doi
        except json.JSONDecodeError:
            continue

    return None


def _extract_doi_from_jsonld(data: Dict) -> Optional[str]:
    """Extract DOI from a JSON-LD object.

    Args:
        data: JSON-LD data dictionary

    Returns:
        DOI string or None
    """
    doi_pattern = r"10\.\d{4,}/[^\s\"'<>)}\]]+[^\s\"'<>)}\].,;:]"

    # Check common DOI fields
    for field in ["doi", "@id", "identifier", "sameAs", "url"]:
        value = data.get(field)
        if value:
            if isinstance(value, str):
                doi_match = re.search(doi_pattern, value)
                if doi_match:
                    return doi_match.group(0).rstrip(".,;:")
            elif isinstance(value, dict):
                # Handle single structured identifier (not in array)
                if value.get("@type") == "PropertyValue" and value.get("propertyID") == "doi":
                    doi_val = value.get("value", "")
                    doi_match = re.search(doi_pattern, doi_val)
                    if doi_match:
                        return doi_match.group(0).rstrip(".,;:")
                # Also check for DOI in nested value field
                nested_val = value.get("value") or value.get("@value")
                if nested_val:
                    doi_match = re.search(doi_pattern, str(nested_val))
                    if doi_match:
                        return doi_match.group(0).rstrip(".,;:")
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        doi_match = re.search(doi_pattern, item)
                        if doi_match:
                            return doi_match.group(0).rstrip(".,;:")
                    elif isinstance(item, dict):
                        # Handle structured identifiers
                        if item.get("@type") == "PropertyValue" and item.get("propertyID") == "doi":
                            doi_val = item.get("value", "")
                            doi_match = re.search(doi_pattern, doi_val)
                            if doi_match:
                                return doi_match.group(0).rstrip(".,;:")

    return None


def extract_metadata_from_html(html: str) -> Dict:
    """Extract publication metadata from HTML page content.

    Extracts title, published date from meta tags and JSON-LD.

    Args:
        html: HTML content as string

    Returns:
        Dictionary with extracted metadata (title, published_date, source)
    """
    result = {
        "title": None,
        "published_date": None,
        "source": None,
    }

    if not html:
        return result

    # 1. Extract title from meta tags
    # Try og:title first
    match = re.search(
        r'<meta\s+[^>]*property\s*=\s*["\']og:title["\']\s+[^>]*content\s*=\s*["\']([^"\']+)["\']',
        html,
        re.IGNORECASE
    )
    if not match:
        match = re.search(
            r'<meta\s+[^>]*content\s*=\s*["\']([^"\']+)["\']\s+[^>]*property\s*=\s*["\']og:title["\']',
            html,
            re.IGNORECASE
        )
    if match:
        result["title"] = match.group(1).strip()

    # Try citation_title if og:title not found
    if not result["title"]:
        match = re.search(
            r'<meta\s+[^>]*name\s*=\s*["\']citation_title["\']\s+[^>]*content\s*=\s*["\']([^"\']+)["\']',
            html,
            re.IGNORECASE
        )
        if not match:
            match = re.search(
                r'<meta\s+[^>]*content\s*=\s*["\']([^"\']+)["\']\s+[^>]*name\s*=\s*["\']citation_title["\']',
                html,
                re.IGNORECASE
            )
        if match:
            result["title"] = match.group(1).strip()

    # Fallback to <title> tag
    if not result["title"]:
        match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            # Clean up common title suffixes
            title = re.sub(r'\s*\|\s*Nature.*$', '', title, flags=re.IGNORECASE)
            title = re.sub(r'\s*\|\s*Science.*$', '', title, flags=re.IGNORECASE)
            title = re.sub(r'\s*-\s*PMC.*$', '', title, flags=re.IGNORECASE)
            result["title"] = title

    # 2. Extract published date
    # Try citation_publication_date
    match = re.search(
        r'<meta\s+[^>]*name\s*=\s*["\']citation_publication_date["\']\s+[^>]*content\s*=\s*["\']([^"\']+)["\']',
        html,
        re.IGNORECASE
    )
    if not match:
        match = re.search(
            r'<meta\s+[^>]*name\s*=\s*["\']citation_date["\']\s+[^>]*content\s*=\s*["\']([^"\']+)["\']',
            html,
            re.IGNORECASE
        )
    if match:
        date_str = match.group(1).strip()
        result["published_date"] = _parse_date_string(date_str)

    # Try article:published_time
    if not result["published_date"]:
        match = re.search(
            r'<meta\s+[^>]*property\s*=\s*["\']article:published_time["\']\s+[^>]*content\s*=\s*["\']([^"\']+)["\']',
            html,
            re.IGNORECASE
        )
        if match:
            date_str = match.group(1).strip()
            result["published_date"] = _parse_date_string(date_str)

    # 3. Extract source/journal from citation_journal_title
    match = re.search(
        r'<meta\s+[^>]*name\s*=\s*["\']citation_journal_title["\']\s+[^>]*content\s*=\s*["\']([^"\']+)["\']',
        html,
        re.IGNORECASE
    )
    if match:
        result["source"] = match.group(1).strip()

    # 4. Try JSON-LD for additional metadata
    for match in re.finditer(r'<script\s+[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.IGNORECASE | re.DOTALL):
        try:
            json_content = match.group(1)
            data = json.loads(json_content)

            if isinstance(data, list):
                for item in data:
                    _extract_metadata_from_jsonld(item, result)
            else:
                _extract_metadata_from_jsonld(data, result)
        except json.JSONDecodeError:
            continue

    return result


def _extract_metadata_from_jsonld(data: Dict, result: Dict) -> None:
    """Extract metadata from JSON-LD object into result dict.

    Args:
        data: JSON-LD data dictionary
        result: Result dictionary to update
    """
    # Extract title
    if not result["title"]:
        headline = data.get("headline") or data.get("name")
        if headline and isinstance(headline, str):
            result["title"] = headline

    # Extract date
    if not result["published_date"]:
        date_str = data.get("datePublished") or data.get("dateCreated")
        if date_str:
            result["published_date"] = _parse_date_string(date_str)

    # Extract source/publisher
    if not result["source"]:
        publisher = data.get("publisher")
        if isinstance(publisher, dict):
            result["source"] = publisher.get("name")
        elif isinstance(publisher, str):
            result["source"] = publisher

        # Also check isPartOf for journal info
        if not result["source"]:
            is_part_of = data.get("isPartOf")
            if isinstance(is_part_of, dict):
                result["source"] = is_part_of.get("name")


def _parse_date_string(date_str: str) -> Optional[str]:
    """Parse various date formats into ISO8601.

    Args:
        date_str: Date string in various formats

    Returns:
        ISO8601 date string or None
    """
    if not date_str:
        return None

    # Already ISO8601
    if re.match(r'^\d{4}-\d{2}-\d{2}', date_str):
        # Normalize to include time component
        if 'T' not in date_str:
            return date_str + "T00:00:00"
        return date_str

    # Try YYYY/MM/DD
    match = re.match(r'^(\d{4})/(\d{1,2})/(\d{1,2})', date_str)
    if match:
        year, month, day = match.groups()
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}T00:00:00"

    # Try Month DD, YYYY
    match = re.match(r'^(\w+)\s+(\d{1,2}),?\s+(\d{4})', date_str)
    if match:
        month_names = {
            "january": "01", "february": "02", "march": "03", "april": "04",
            "may": "05", "june": "06", "july": "07", "august": "08",
            "september": "09", "october": "10", "november": "11", "december": "12",
            "jan": "01", "feb": "02", "mar": "03", "apr": "04",
            "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
        }
        month_str, day, year = match.groups()
        month = month_names.get(month_str.lower())
        if month:
            return f"{year}-{month}-{day.zfill(2)}T00:00:00"

    # Try DD Month YYYY
    match = re.match(r'^(\d{1,2})\s+(\w+)\s+(\d{4})', date_str)
    if match:
        month_names = {
            "january": "01", "february": "02", "march": "03", "april": "04",
            "may": "05", "june": "06", "july": "07", "august": "08",
            "september": "09", "october": "10", "november": "11", "december": "12",
            "jan": "01", "feb": "02", "mar": "03", "apr": "04",
            "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
        }
        day, month_str, year = match.groups()
        month = month_names.get(month_str.lower())
        if month:
            return f"{year}-{month}-{day.zfill(2)}T00:00:00"

    return None


def resolve_pmid(pmid: str) -> Dict:
    """Resolve PMID to metadata using NCBI E-utilities.

    Args:
        pmid: PubMed ID

    Returns:
        Dictionary with title, source, published_date, abstract, url
    """
    result = {
        "pmid": pmid,
        "title": None,
        "source": None,
        "published_date": None,
        "abstract": None,
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "doi": None,
        "resolution_error": None,
    }

    try:
        # Fetch from NCBI E-utilities (efetch for full records)
        efetch_url = (
            f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            f"?db=pubmed&id={pmid}&retmode=xml"
        )

        response = requests.get(efetch_url, timeout=30)
        response.raise_for_status()

        xml_content = response.text

        # Parse XML to extract fields
        # Title
        title_match = re.search(r"<ArticleTitle>(.+?)</ArticleTitle>", xml_content, re.DOTALL)
        if title_match:
            result["title"] = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()

        # Journal name (source)
        journal_match = re.search(r"<Title>(.+?)</Title>", xml_content)
        if journal_match:
            result["source"] = journal_match.group(1).strip()

        # Published date
        pub_date_match = re.search(
            r"<PubDate>.*?<Year>(\d{4})</Year>.*?(?:<Month>(\d{1,2}|\w+)</Month>)?.*?(?:<Day>(\d{1,2})</Day>)?.*?</PubDate>",
            xml_content,
            re.DOTALL,
        )
        if pub_date_match:
            year = pub_date_match.group(1)
            month = pub_date_match.group(2) or "01"
            day = pub_date_match.group(3) or "01"

            # Convert month name to number if needed
            month_names = {
                "jan": "01", "feb": "02", "mar": "03", "apr": "04",
                "may": "05", "jun": "06", "jul": "07", "aug": "08",
                "sep": "09", "oct": "10", "nov": "11", "dec": "12",
            }
            if month.lower() in month_names:
                month = month_names[month.lower()]
            else:
                month = month.zfill(2)

            result["published_date"] = f"{year}-{month}-{day.zfill(2)}T00:00:00"

        # Abstract
        abstract_match = re.search(r"<AbstractText[^>]*>(.+?)</AbstractText>", xml_content, re.DOTALL)
        if abstract_match:
            result["abstract"] = re.sub(r"<[^>]+>", "", abstract_match.group(1)).strip()

        # DOI
        doi_match = re.search(r'<ArticleId IdType="doi">(.+?)</ArticleId>', xml_content)
        if doi_match:
            result["doi"] = doi_match.group(1).strip()

        title_preview = str(result["title"])[:80] if result.get("title") else "No title"
        logger.info("Resolved PMID %s: %s", pmid, title_preview)

    except requests.RequestException as e:
        result["resolution_error"] = f"NCBI request failed: {str(e)}"
        logger.warning("Failed to resolve PMID %s: %s", pmid, e)
    except Exception as e:
        result["resolution_error"] = f"Parse error: {str(e)}"
        logger.warning("Failed to parse PMID %s response: %s", pmid, e)

    return result


def resolve_doi(doi: str) -> Dict:
    """Resolve DOI to metadata using Crossref API.

    Args:
        doi: Digital Object Identifier

    Returns:
        Dictionary with title, source, published_date, abstract, url
    """
    result = {
        "doi": doi,
        "title": None,
        "source": None,
        "published_date": None,
        "abstract": None,
        "url": f"https://doi.org/{doi}",
        "pmid": None,
        "resolution_error": None,
    }

    try:
        # Fetch from Crossref API
        crossref_url = f"https://api.crossref.org/works/{doi}"
        headers = {
            "User-Agent": "SpotItEarly/1.0 (mailto:support@spotitearly.com)",
        }

        response = requests.get(crossref_url, headers=headers, timeout=30)
        response.raise_for_status()

        data = response.json().get("message", {})

        # Title
        titles = data.get("title", [])
        if titles:
            result["title"] = titles[0]

        # Source (container-title is the journal name)
        containers = data.get("container-title", [])
        if containers:
            result["source"] = containers[0]

        # Published date
        published = data.get("published-print") or data.get("published-online") or data.get("created")
        if published:
            date_parts = published.get("date-parts", [[]])[0]
            if date_parts:
                year = date_parts[0]
                month = date_parts[1] if len(date_parts) > 1 else 1
                day = date_parts[2] if len(date_parts) > 2 else 1
                result["published_date"] = f"{year}-{str(month).zfill(2)}-{str(day).zfill(2)}T00:00:00"

        # Abstract (often not available in Crossref)
        abstract = data.get("abstract")
        if abstract:
            # Remove JATS/HTML tags
            result["abstract"] = re.sub(r"<[^>]+>", "", abstract).strip()

        # URL (prefer DOI URL)
        result["url"] = data.get("URL") or f"https://doi.org/{doi}"

        title_preview = str(result["title"])[:80] if result.get("title") else "No title"
        logger.info("Resolved DOI %s: %s", doi, title_preview)

    except requests.RequestException as e:
        result["resolution_error"] = f"Crossref request failed: {str(e)}"
        logger.warning("Failed to resolve DOI %s: %s", doi, e)
    except Exception as e:
        result["resolution_error"] = f"Parse error: {str(e)}"
        logger.warning("Failed to parse DOI %s response: %s", doi, e)

    return result


def resolve_url(url: str, force_doi_map: Optional[Dict[str, str]] = None) -> Dict:
    """Resolve URL to metadata.

    Resolution strategy:
    1. Check force-DOI map for manual override
    2. Try to extract DOI/PMID from URL path
    3. Fetch HTML and extract DOI from meta tags / JSON-LD
    4. If DOI found, resolve via Crossref
    5. Fall back to extracting metadata from HTML

    Args:
        url: Publication URL
        force_doi_map: Optional dict mapping URLs to DOIs for stubborn cases

    Returns:
        Dictionary with title, source, published_date, abstract, url
    """
    result = {
        "url": url,
        "title": None,
        "source": None,
        "published_date": None,
        "abstract": None,
        "doi": None,
        "pmid": None,
        "resolution_error": None,
    }

    # Use global map if not provided
    if force_doi_map is None:
        force_doi_map = _FORCE_DOI_MAP

    # 1. Check force-DOI map first
    if force_doi_map and url in force_doi_map:
        doi = force_doi_map[url]
        logger.info("Using force-DOI map: %s -> %s", url, doi)
        doi_result = resolve_doi(doi)
        result.update(doi_result)
        result["url"] = url  # Keep original URL
        return result

    # 2. Try to extract DOI from URL path
    doi = extract_doi_from_url(url)
    if doi:
        logger.info("Extracted DOI %s from URL path: %s", doi, url)
        doi_result = resolve_doi(doi)
        result.update(doi_result)
        result["url"] = url  # Keep original URL
        return result

    # 3. Try to extract PMID from URL path
    pmid = extract_pmid_from_url(url)
    if pmid:
        logger.info("Extracted PMID %s from URL path: %s", pmid, url)
        pmid_result = resolve_pmid(pmid)
        result.update(pmid_result)
        result["url"] = url  # Keep original URL
        return result

    # 4. Fetch HTML and try to extract DOI from page content
    logger.info("No DOI/PMID in URL path, fetching HTML from: %s", url)
    html = fetch_html_with_browser_ua(url)

    if html:
        # Try to extract DOI from HTML
        doi = extract_doi_from_html(html)
        if doi:
            logger.info("Extracted DOI %s from HTML content: %s", doi, url)
            doi_result = resolve_doi(doi)
            result.update(doi_result)
            result["url"] = url  # Keep original URL
            return result

        # 5. Fall back to extracting metadata directly from HTML
        logger.info("No DOI found in HTML, extracting metadata directly: %s", url)
        html_metadata = extract_metadata_from_html(html)

        if html_metadata.get("title"):
            result["title"] = html_metadata["title"]
        if html_metadata.get("published_date"):
            result["published_date"] = html_metadata["published_date"]
        if html_metadata.get("source"):
            result["source"] = html_metadata["source"]

    # Determine source from domain if not already set
    if not result["source"]:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        domain_to_source = {
            "nature.com": "Nature",
            "science.org": "Science",
            "cell.com": "Cell",
            "nejm.org": "NEJM",
            "thelancet.com": "The Lancet",
            "plos.org": "PLOS",
            "biorxiv.org": "bioRxiv",
            "medrxiv.org": "medRxiv",
            "arxiv.org": "arXiv",
            "pubmed.ncbi.nlm.nih.gov": "PubMed",
            "ncbi.nlm.nih.gov": "NCBI",
            "aacrjournals.org": "AACR Journals",
            "jci.org": "JCI",
            "pnas.org": "PNAS",
            "jamanetwork.com": "JAMA",
            "bmj.com": "BMJ",
            "wiley.com": "Wiley",
            "springer.com": "Springer",
            "elsevier.com": "Elsevier",
            "sciencedirect.com": "ScienceDirect",
        }

        for domain_pattern, source_name in domain_to_source.items():
            if domain_pattern in domain:
                result["source"] = source_name
                break

    # Set resolution error only if we couldn't get meaningful data
    if not result.get("title") and not result.get("doi"):
        result["resolution_error"] = "Could not extract DOI or metadata from URL"
        logger.warning("Could not resolve URL to meaningful metadata: %s", url)
    elif not result.get("doi"):
        result["resolution_error"] = "No DOI found; using HTML-extracted metadata"
        logger.info("Resolved URL with HTML metadata (no DOI): %s", url)

    return result


def resolve_title(title: str) -> Dict:
    """Resolve a publication title to metadata by searching PubMed.

    Performs an exact title search on PubMed via NCBI E-utilities,
    then fetches full metadata for the top result.

    Args:
        title: Publication title to search for

    Returns:
        Dictionary with title, source, published_date, abstract, url, doi, pmid
    """
    result = {
        "title": title,
        "source": None,
        "published_date": None,
        "abstract": None,
        "url": None,
        "doi": None,
        "pmid": None,
        "resolution_error": None,
    }

    try:
        # Step 1: Search PubMed by exact title
        esearch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        search_params = {
            "db": "pubmed",
            "term": f'"{title}"[Title]',
            "retmax": 3,
            "retmode": "json",
        }

        response = requests.get(esearch_url, params=search_params, timeout=30)
        response.raise_for_status()

        search_data = response.json()
        id_list = search_data.get("esearchresult", {}).get("idlist", [])

        if not id_list:
            # Retry with a looser search (title words without exact match)
            search_params["term"] = f"{title}[Title]"
            response = requests.get(esearch_url, params=search_params, timeout=30)
            response.raise_for_status()
            search_data = response.json()
            id_list = search_data.get("esearchresult", {}).get("idlist", [])

        if not id_list:
            result["resolution_error"] = "No PubMed results found for title"
            logger.warning("No PubMed results for title: %s", title[:80])
            return result

        # Step 2: Use the first PMID to get full metadata via resolve_pmid
        pmid = id_list[0]
        logger.info("Title search found PMID %s for: %s", pmid, title[:80])

        resolved = resolve_pmid(pmid)

        # Merge resolved data into result (keep original title as fallback)
        result["pmid"] = pmid
        result["title"] = resolved.get("title") or title
        result["source"] = resolved.get("source")
        result["published_date"] = resolved.get("published_date")
        result["abstract"] = resolved.get("abstract")
        result["url"] = resolved.get("url") or f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        result["doi"] = resolved.get("doi")
        result["resolution_error"] = resolved.get("resolution_error")

    except requests.RequestException as e:
        result["resolution_error"] = f"PubMed search failed: {str(e)}"
        logger.warning("Failed to search PubMed for title: %s", e)
    except Exception as e:
        result["resolution_error"] = f"Title resolution error: {str(e)}"
        logger.warning("Failed to resolve title '%s': %s", title[:80], e)

    return result


def resolve_seed(seed: Dict) -> Dict:
    """Resolve a single seed to publication metadata.

    Args:
        seed: Dictionary with "type" (url|doi|pmid|title) and "value"

    Returns:
        Dictionary with resolved metadata
    """
    seed_type = str(seed.get("type", "")).lower()
    raw_value = seed.get("value", "")
    value = str(raw_value) if raw_value is not None else ""

    if not value:
        return {
            "resolution_error": "Missing value in seed",
            "original_seed": seed,
        }

    if seed_type == "pmid":
        result = resolve_pmid(value)
    elif seed_type == "doi":
        result = resolve_doi(value)
    elif seed_type == "url":
        result = resolve_url(value)
    elif seed_type == "title":
        result = resolve_title(value)
    else:
        return {
            "resolution_error": f"Unknown seed type: {seed_type}",
            "original_seed": seed,
        }

    result["original_seed"] = seed
    return result


def generate_publication_id(seed: Dict, resolved: Dict) -> str:
    """Generate a stable publication ID from seed and resolved data.

    Args:
        seed: Original seed dict
        resolved: Resolved metadata dict

    Returns:
        SHA256 hash as publication ID
    """
    # Use DOI, PMID, or URL as the primary identifier
    identifier = (
        resolved.get("doi")
        or resolved.get("pmid")
        or resolved.get("url")
        or seed.get("value", "unknown")
    )
    identifier = str(identifier) if identifier is not None else "unknown"

    # Generate stable hash
    hash_input = f"seed:{identifier}"
    return hashlib.sha256(hash_input.encode("utf-8")).hexdigest()


def build_paper_for_review(resolved: Dict, publication_id: str) -> Dict:
    """Build a paper dictionary in the format expected by tri-model reviewers.

    Args:
        resolved: Resolved metadata dictionary
        publication_id: Generated publication ID

    Returns:
        Paper dictionary for tri-model review
    """
    return {
        "id": publication_id,
        "title": resolved.get("title") or "Unknown Title",
        "source": resolved.get("source") or "Unknown Source",
        "date": resolved.get("published_date"),
        "url": resolved.get("url"),
        "raw_text": resolved.get("abstract") or "",
        # Additional metadata for debugging
        "doi": resolved.get("doi"),
        "pmid": resolved.get("pmid"),
    }


def review_paper_with_tri_model(
    paper: Dict,
    available_reviewers: List[str],
) -> Optional[Dict]:
    """Review a single paper using tri-model system.

    Args:
        paper: Paper dictionary with title, source, raw_text
        available_reviewers: List of available reviewers (claude, gemini)

    Returns:
        Dictionary with review results, or None if all reviewers failed
    """
    from tri_model.reviewers import claude_review, gemini_review
    from tri_model.evaluator import gpt_evaluate
    from tri_model.credibility import score_paper_credibility

    claude_result = None
    gemini_result = None

    # Call Claude reviewer if available
    if "claude" in available_reviewers:
        try:
            claude_result = claude_review(paper)
            if not claude_result.get("success"):
                logger.warning(
                    "Claude review failed for %s: %s",
                    paper.get("id", "unknown")[:16],
                    claude_result.get("error"),
                )
        except Exception as e:
            logger.error("Claude reviewer exception for %s: %s", paper.get("id", "unknown")[:16], e)

    # Call Gemini reviewer if available
    if "gemini" in available_reviewers:
        try:
            gemini_result = gemini_review(paper)
            if not gemini_result.get("success"):
                logger.warning(
                    "Gemini review failed for %s: %s",
                    paper.get("id", "unknown")[:16],
                    gemini_result.get("error"),
                )
        except Exception as e:
            logger.error("Gemini reviewer exception for %s: %s", paper.get("id", "unknown")[:16], e)

    # If both reviewers failed, skip this paper
    if (claude_result is None or not claude_result.get("success")) and \
       (gemini_result is None or not gemini_result.get("success")):
        logger.warning("All reviewers failed for %s, skipping", paper.get("id", "unknown")[:16])
        return None

    # Call GPT evaluator
    try:
        gpt_result = gpt_evaluate(paper, claude_result, gemini_result)
        if not gpt_result.get("success"):
            logger.warning(
                "GPT evaluator failed for %s: %s",
                paper.get("id", "unknown")[:16],
                gpt_result.get("error"),
            )
            return None
    except Exception as e:
        logger.error("GPT evaluator exception for %s: %s", paper.get("id", "unknown")[:16], e)
        return None

    # Score credibility
    credibility_result = None
    try:
        credibility_result = score_paper_credibility(paper)
        if credibility_result.get("error"):
            logger.warning(
                "Credibility scoring had issues for %s: %s",
                paper.get("id", "unknown")[:16],
                credibility_result.get("error"),
            )
    except Exception as e:
        logger.error("Credibility scoring exception for %s: %s", paper.get("id", "unknown")[:16], e)
        credibility_result = {
            "credibility_score": None,
            "credibility_reason": f"Exception: {str(e)}",
            "credibility_confidence": "low",
            "credibility_signals": {},
            "error": str(e)
        }

    # Assemble full result
    return {
        "publication_id": paper.get("id"),
        "title": paper.get("title"),
        "source": paper.get("source"),
        "published_date": paper.get("date"),
        "url": paper.get("url"),
        "claude_review": claude_result,
        "gemini_review": gemini_result,
        "gpt_evaluation": gpt_result,
        "credibility": credibility_result,
    }


def write_tri_model_events(
    run_id: str,
    mode: str,
    results: List[Dict],
    output_path: Path,
) -> int:
    """Write tri-model events to JSONL file.

    Args:
        run_id: Run identifier
        mode: Run mode
        results: List of review result dictionaries
        output_path: Path to output JSONL file

    Returns:
        Number of events written
    """
    events_written = 0

    with open(output_path, "w", encoding="utf-8") as f:
        prompt_meta = _get_prompt_metadata()
        for result in results:
            if result is None:
                continue

            # Extract evaluation data
            eval_data = result.get("gpt_evaluation", {}).get("evaluation", {})
            cred_data = result.get("credibility", {})

            # Extract review data safely
            claude_review = None
            if result.get("claude_review") and result["claude_review"].get("success"):
                claude_review = result["claude_review"].get("review")

            gemini_review = None
            if result.get("gemini_review") and result["gemini_review"].get("success"):
                gemini_review = result["gemini_review"].get("review")

            # Extract latencies
            claude_latency = result["claude_review"].get("latency_ms") if result.get("claude_review") and result["claude_review"].get("success") else None
            gemini_latency = result["gemini_review"].get("latency_ms") if result.get("gemini_review") and result["gemini_review"].get("success") else None
            gpt_latency = result.get("gpt_evaluation", {}).get("latency_ms")

            # Build event record (matching existing tri_model_events.jsonl format)
            event = {
                "run_id": run_id,
                "mode": mode,
                "publication_id": result.get("publication_id"),
                "title": result.get("title"),
                "source": result.get("source"),
                "published_date": result.get("published_date"),
                "url": result.get("url"),
                # Individual reviews
                "claude_review": claude_review,
                "gemini_review": gemini_review,
                "gpt_eval": eval_data,
                # Flattened evaluation fields
                "final_relevancy_score": eval_data.get("final_relevancy_score"),
                "final_relevancy_reason": eval_data.get("final_relevancy_reason"),
                "final_signals": eval_data.get("final_signals"),
                "final_summary": eval_data.get("final_summary"),
                "agreement_level": eval_data.get("agreement_level"),
                "disagreements": eval_data.get("disagreements"),
                "evaluator_rationale": eval_data.get("evaluator_rationale"),
                "confidence": eval_data.get("confidence"),
                # Prompt/model metadata
                "prompt_versions": {
                    "claude": prompt_meta["prompt_version"],
                    "gemini": prompt_meta["prompt_version"],
                    "gpt": prompt_meta["prompt_version"],
                    "rubric_version": prompt_meta["rubric_version"],
                    "prompt_hash": prompt_meta["prompt_hash"],
                    "prompt_hashes": prompt_meta["prompt_hashes"],
                },
                "model_names": {
                    "claude": result.get("claude_review", {}).get("model") if result.get("claude_review") and result["claude_review"].get("success") else None,
                    "gemini": result.get("gemini_review", {}).get("model") if result.get("gemini_review") and result["gemini_review"].get("success") else None,
                    "gpt": result.get("gpt_evaluation", {}).get("model"),
                },
                # Latencies
                "claude_latency_ms": claude_latency,
                "gemini_latency_ms": gemini_latency,
                "gpt_latency_ms": gpt_latency,
                # Credibility fields
                "credibility_score": cred_data.get("credibility_score"),
                "credibility_reason": cred_data.get("credibility_reason"),
                "credibility_confidence": cred_data.get("credibility_confidence"),
                "credibility_signals": cred_data.get("credibility_signals"),
                # Timestamp
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

            f.write(json.dumps(event, ensure_ascii=False) + "\n")
            events_written += 1

    logger.info("Wrote %d events to %s", events_written, output_path)
    return events_written


def write_manifest(
    run_id: str,
    mode: str,
    output_dir: Path,
    total_seeds: int,
    resolved_count: int,
    failed_resolution_count: int,
    scored_count: int,
    reviewer_failures_count: int,
    available_reviewers: List[str],
) -> Dict:
    """Write manifest file with run metadata.

    Args:
        run_id: Run identifier
        mode: Run mode
        output_dir: Output directory
        total_seeds: Total number of input seeds
        resolved_count: Successfully resolved seeds
        failed_resolution_count: Failed resolution count
        scored_count: Successfully scored papers
        reviewer_failures_count: Number of reviewer failures
        available_reviewers: List of available reviewers

    Returns:
        Manifest data dictionary
    """
    now = datetime.now()

    manifest_data = {
        **_get_prompt_metadata(),
        "run_id": run_id,
        "run_type": "seed-papers",
        "mode": mode,
        "generated_at": now.isoformat(),
        # For seed papers, window is just the run time
        "window_start": now.isoformat(),
        "window_end": now.isoformat(),
        "window_mode": "seed_papers",
        "counts": {
            "total_seeds": total_seeds,
            "resolved": resolved_count,
            "failed_resolution": failed_resolution_count,
            "scored": scored_count,
            "reviewer_failures": reviewer_failures_count,
            "gpt_evaluations": scored_count,
        },
        "reviewers_used": available_reviewers,
        "seed_scoring_version": SEED_SCORING_VERSION,
        "local_output_paths": {
            "tri_model_events": str(output_dir / "tri_model_events.jsonl"),
            "manifest": str(output_dir / "manifest.json"),
        },
    }

    # Write manifest
    manifest_path = output_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)

    logger.info("Wrote manifest to %s", manifest_path)
    return manifest_data


def ingest_to_backend(
    output_dir: Path,
    run_id: str,
    mode: str,
    backend_url: str,
    backend_api_key: str,
    chunk_size: int = 100,
    strict: bool = False,
) -> bool:
    """Ingest outputs to backend.

    Args:
        output_dir: Directory containing manifest.json and tri_model_events.jsonl
        run_id: Run identifier
        mode: Run mode
        backend_url: Backend URL
        backend_api_key: Backend API key
        chunk_size: Batch size for events ingestion
        strict: If True, raise on failure

    Returns:
        True if successful, False otherwise
    """
    try:
        # Import ingestion functions from existing script
        from scripts.ingest_to_backend import (
            ingest_manifest,
            ingest_tri_model_events,
            load_json_file,
            load_jsonl_file,
        )
    except ImportError:
        # Fallback: define inline
        logger.warning("Could not import ingest_to_backend, using inline implementation")
        return _ingest_to_backend_inline(
            output_dir, run_id, mode, backend_url, backend_api_key, chunk_size, strict
        )

    try:
        # Load data files
        manifest_data = load_json_file(output_dir / "manifest.json")
        events = load_jsonl_file(output_dir / "tri_model_events.jsonl")

        # Ingest manifest
        manifest_result = ingest_manifest(
            backend_url=backend_url,
            api_key=backend_api_key,
            manifest_data=manifest_data,
            timeout=60,
            retries=3,
            dry_run=False,
        )

        if not manifest_result["success"]:
            logger.error("Backend manifest ingestion failed")
            if strict:
                raise RuntimeError("Manifest ingestion failed")
            return False

        # Ingest tri-model events
        events_result = ingest_tri_model_events(
            backend_url=backend_url,
            api_key=backend_api_key,
            run_id=run_id,
            mode=mode,
            events=events,
            chunk_size=chunk_size,
            timeout=60,
            retries=3,
            dry_run=False,
        )

        if not events_result["success"]:
            logger.error("Backend tri-model events ingestion failed")
            if strict:
                raise RuntimeError("Events ingestion failed")
            return False

        logger.info("Backend ingestion successful (%d events)", len(events))
        return True

    except Exception as e:
        logger.error("Backend ingestion exception: %s", e)
        if strict:
            raise
        return False


def _ingest_to_backend_inline(
    output_dir: Path,
    run_id: str,
    mode: str,
    backend_url: str,
    backend_api_key: str,
    chunk_size: int = 100,
    strict: bool = False,
) -> bool:
    """Inline implementation of backend ingestion."""
    headers = {
        "X-API-Key": backend_api_key,
        "Content-Type": "application/json",
    }

    # Load manifest
    manifest_path = output_dir / "manifest.json"
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)

    # Ingest manifest
    try:
        response = requests.post(
            f"{backend_url}/ingest/run",
            headers=headers,
            json=manifest_data,
            timeout=60,
        )
        response.raise_for_status()
        logger.info("Manifest ingested successfully")
    except requests.RequestException as e:
        logger.error("Manifest ingestion failed: %s", e)
        if strict:
            raise
        return False

    # Load events
    events = []
    events_path = output_dir / "tri_model_events.jsonl"
    with open(events_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    # Ingest events in chunks
    for i in range(0, len(events), chunk_size):
        chunk = events[i:i + chunk_size]
        payload = {
            "run_id": run_id,
            "mode": mode,
            "events": chunk,
        }

        try:
            response = requests.post(
                f"{backend_url}/ingest/tri-model-events",
                headers=headers,
                json=payload,
                timeout=60,
            )
            response.raise_for_status()
            logger.info("Ingested events chunk %d-%d", i, i + len(chunk))
        except requests.RequestException as e:
            logger.error("Events ingestion failed for chunk %d: %s", i // chunk_size, e)
            if strict:
                raise
            return False

    logger.info("Backend ingestion complete (%d events)", len(events))
    return True


def main() -> int:
    """Main entrypoint for seed papers scoring."""
    parser = argparse.ArgumentParser(
        description="Score seed papers using tri-model scoring pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example usage:
    # Basic usage
    python scripts/score_seed_papers.py --input seeds.json

    # With backend ingestion
    python scripts/score_seed_papers.py --input seeds.json --ingest-backend

    # Custom run ID
    python scripts/score_seed_papers.py --input seeds.json --run-id my-seeds-run-1

Input format (seeds.json):
    [
        {"type": "pmid", "value": "39385123"},
        {"type": "doi", "value": "10.1038/s41586-024-07051-0"},
        {"type": "url", "value": "https://www.nature.com/articles/s41586-024-07051-0"}
    ]
        """,
    )
    parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="Path to seeds.json file with list of seed papers",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="tri-model-seeds",
        help="Run mode (default: tri-model-seeds)",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        help="Run identifier (default: tri-model-seeds-YYYY-MM-DD)",
    )
    parser.add_argument(
        "--max-papers",
        type=int,
        help="Maximum number of papers to process",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Output directory (default: data/outputs/<mode>/<run_id>/)",
    )
    parser.add_argument(
        "--ingest-backend",
        action="store_true",
        help="Ingest outputs to backend after scoring",
    )
    parser.add_argument(
        "--backend-url",
        type=str,
        help="Backend URL (default: from BACKEND_URL env var)",
    )
    parser.add_argument(
        "--backend-api-key",
        type=str,
        help="Backend API key (default: from BACKEND_API_KEY env var)",
    )
    parser.add_argument(
        "--ingest-strict",
        action="store_true",
        help="Exit with error if backend ingestion fails",
    )
    parser.add_argument(
        "--lookback-hours",
        type=int,
        help="Ignored for seed mode (for CLI compatibility)",
    )
    parser.add_argument(
        "--force-doi-map",
        type=str,
        help="Path to JSON file mapping stubborn URLs to DOIs (format: {url: doi})",
    )

    args = parser.parse_args()

    # Load force-DOI map if provided
    if args.force_doi_map:
        force_doi_map_path = Path(args.force_doi_map)
        if force_doi_map_path.exists():
            load_force_doi_map(str(force_doi_map_path))
        else:
            logger.warning("Force-DOI map file not found: %s", force_doi_map_path)

    # Validate tri-model configuration
    try:
        from config.tri_model_config import (
            is_tri_model_enabled,
            get_available_reviewers,
            validate_config,
            normalize_validation_result,
        )
    except ImportError as e:
        logger.error("Failed to import tri-model config: %s", e)
        print("\n❌ ERROR: Could not import tri-model configuration.")
        print("   Make sure you're running from the project root directory.\n")
        return 1

    if not is_tri_model_enabled():
        logger.error("Tri-model system is not enabled. Set TRI_MODEL_MINI_DAILY=true")
        print("\n❌ ERROR: Tri-model system is not enabled.")
        print("   Set environment variable: TRI_MODEL_MINI_DAILY=true\n")
        return 1

    # Validate configuration
    raw_validation_result = validate_config()
    validation_result = normalize_validation_result(raw_validation_result)

    if not validation_result["valid"]:
        print("\n❌ ERROR: Configuration validation failed:")
        for error in validation_result["errors"]:
            # Sanitize any secrets from error messages
            safe_error = error
            for env_var in ["CLAUDE_API_KEY", "GEMINI_API_KEY", "SPOTITEARLY_LLM_API_KEY"]:
                key = os.getenv(env_var)
                if key:
                    safe_error = safe_error.replace(key, "***")
            print(f"   - {safe_error}")
        print()
        return 1

    available_reviewers = get_available_reviewers()
    logger.info("Available reviewers: %s", available_reviewers)

    # Load seeds
    input_path = Path(args.input)
    if not input_path.exists():
        logger.error("Input file not found: %s", input_path)
        print(f"\n❌ ERROR: Input file not found: {input_path}\n")
        return 1

    try:
        with open(input_path, "r", encoding="utf-8") as f:
            seeds = json.load(f)
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in input file: %s", e)
        print(f"\n❌ ERROR: Invalid JSON in input file: {e}\n")
        return 1

    if not isinstance(seeds, list):
        logger.error("Input must be a JSON array of seed objects")
        print("\n❌ ERROR: Input must be a JSON array of seed objects\n")
        return 1

    # Apply max-papers cap if specified
    if args.max_papers and len(seeds) > args.max_papers:
        logger.info("Applying max-papers cap: %d → %d", len(seeds), args.max_papers)
        seeds = seeds[:args.max_papers]

    total_seeds = len(seeds)
    logger.info("Loaded %d seeds from %s", total_seeds, input_path)

    # Generate run ID
    run_id = args.run_id or f"tri-model-seeds-{datetime.now().strftime('%Y-%m-%d')}"

    # Determine output directory
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = Path("data/outputs") / args.mode / run_id

    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Output directory: %s", output_dir)

    # Print execution plan
    print("\n" + "=" * 70)
    print("Seed Papers Scoring - Tri-Model Pipeline")
    print("=" * 70)
    print(f"Run ID:          {run_id}")
    print(f"Mode:            {args.mode}")
    print(f"Input file:      {input_path}")
    print(f"Total seeds:     {total_seeds}")
    print(f"Reviewers:       {', '.join(available_reviewers)}")
    print(f"Output dir:      {output_dir}")
    if args.ingest_backend:
        print(f"Backend ingest:  Enabled")
    print("=" * 70 + "\n")

    # Phase 1: Resolve seeds
    logger.info("Phase 1: Resolving seed papers")
    resolved_papers = []
    resolution_failures = []

    for i, seed in enumerate(seeds, 1):
        seed_value = seed.get("value", "")
        logger.info("Resolving seed %d/%d: %s=%s", i, total_seeds, seed.get("type"), str(seed_value)[:50])
        resolved = resolve_seed(seed)

        if resolved.get("resolution_error") and not resolved.get("title"):
            logger.warning("Failed to resolve seed: %s", resolved.get("resolution_error"))
            resolution_failures.append(resolved)
        else:
            resolved_papers.append(resolved)

        # Rate limiting for NCBI/Crossref APIs
        time.sleep(0.5)

    resolved_count = len(resolved_papers)
    failed_resolution_count = len(resolution_failures)

    logger.info(
        "Resolution complete: %d resolved, %d failed",
        resolved_count,
        failed_resolution_count,
    )

    if resolved_count == 0:
        logger.error("No seeds could be resolved. Aborting.")
        print("\n❌ ERROR: No seeds could be resolved. Check the input file.\n")
        return 1

    # Phase 2: Build papers and run tri-model scoring
    logger.info("Phase 2: Running tri-model scoring on %d papers", resolved_count)
    results = []
    reviewer_failures_count = 0

    for i, resolved in enumerate(resolved_papers, 1):
        publication_id = generate_publication_id(resolved.get("original_seed", {}), resolved)
        paper = build_paper_for_review(resolved, publication_id)

        logger.info(
            "Scoring paper %d/%d: %s",
            i,
            resolved_count,
            paper["title"][:60],
        )

        # Skip if no content to review (no title and no abstract)
        if paper["title"] == "Unknown Title" and not paper.get("raw_text"):
            logger.warning("Skipping paper with no title/abstract: %s", resolved.get("url"))
            reviewer_failures_count += 1
            continue

        result = review_paper_with_tri_model(paper, available_reviewers)

        if result is None:
            reviewer_failures_count += 1
            continue

        # Add URL to result (for backend compatibility)
        result["url"] = paper.get("url")
        results.append(result)

    scored_count = len(results)
    logger.info(
        "Scoring complete: %d scored, %d failures",
        scored_count,
        reviewer_failures_count,
    )

    # Phase 3: Write output artifacts
    logger.info("Phase 3: Writing output artifacts")

    # Write tri_model_events.jsonl
    events_path = output_dir / "tri_model_events.jsonl"
    write_tri_model_events(run_id, args.mode, results, events_path)

    # Write manifest.json
    manifest_data = write_manifest(
        run_id=run_id,
        mode=args.mode,
        output_dir=output_dir,
        total_seeds=total_seeds,
        resolved_count=resolved_count,
        failed_resolution_count=failed_resolution_count,
        scored_count=scored_count,
        reviewer_failures_count=reviewer_failures_count,
        available_reviewers=available_reviewers,
    )

    # Phase 4: Backend ingestion (optional)
    ingestion_failed = False
    if args.ingest_backend:
        logger.info("Phase 4: Ingesting outputs to backend")

        backend_url = args.backend_url or os.getenv("BACKEND_URL")
        backend_api_key = args.backend_api_key or os.getenv("BACKEND_API_KEY")

        if not backend_url or not backend_api_key:
            logger.warning("Backend ingestion requested but credentials not provided")
            print("\n⚠️  WARNING: Backend ingestion skipped (missing BACKEND_URL or BACKEND_API_KEY)")
            if args.ingest_strict:
                print("   Exiting with error code due to --ingest-strict flag\n")
                return 1
        else:
            success = ingest_to_backend(
                output_dir=output_dir,
                run_id=run_id,
                mode=args.mode,
                backend_url=backend_url.rstrip("/"),
                backend_api_key=backend_api_key,
                chunk_size=100,
                strict=args.ingest_strict,
            )

            if not success:
                ingestion_failed = True
                print("\n⚠️  WARNING: Backend ingestion failed (see logs above)")
                if args.ingest_strict:
                    print("   Exiting with error code due to --ingest-strict flag\n")
                    return 1
            else:
                print(f"\n✓ Backend ingestion complete ({scored_count} events)")

    # Final summary
    print("\n" + "=" * 70)
    print("SEED PAPERS SCORING COMPLETE")
    print("=" * 70)
    print(f"Run ID:           {run_id}")
    print(f"Total seeds:      {total_seeds}")
    print(f"Resolved:         {resolved_count}")
    print(f"Failed resolve:   {failed_resolution_count}")
    print(f"Scored:           {scored_count}")
    print(f"Reviewer failures:{reviewer_failures_count}")
    print(f"Output dir:       {output_dir}")
    print("=" * 70 + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
