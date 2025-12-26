"""Commercial signal extraction for publications.

This module detects sponsor and company affiliation signals in publication text
using deterministic pattern matching. No LLMs, no scoring, no interpretation.
"""

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Sponsor signal patterns (conservative, precision-first)
SPONSOR_PATTERNS = [
    r"funded by\s+([A-Z][A-Za-z0-9\s&,.-]{2,50})",
    r"supported by\s+([A-Z][A-Za-z0-9\s&,.-]{2,50})",
    r"grant from\s+([A-Z][A-Za-z0-9\s&,.-]{2,50})",
    r"sponsored by\s+([A-Z][A-Za-z0-9\s&,.-]{2,50})",
    r"funding provided by\s+([A-Z][A-Za-z0-9\s&,.-]{2,50})",
    r"research supported by\s+([A-Z][A-Za-z0-9\s&,.-]{2,50})",
]

# Company affiliation patterns (conservative, precision-first)
COMPANY_PATTERNS = [
    r"employee of\s+([A-Z][A-Za-z0-9\s&,.-]{2,50})",
    r"consultant for\s+([A-Z][A-Za-z0-9\s&,.-]{2,50})",
    r"affiliated with\s+([A-Z][A-Za-z0-9\s&,.-]{2,50})",
    r"conflict of interest.*?([A-Z][A-Za-z0-9\s&,.-]{2,50})",
    r"received compensation from\s+([A-Z][A-Za-z0-9\s&,.-]{2,50})",
    r"disclosure.*?([A-Z][A-Za-z0-9\s&,.-]{2,50})",
]


def extract_commercial_signals(text: str) -> dict:
    """Extract commercial signals from publication text.

    Uses conservative pattern matching to detect sponsor and company
    affiliation signals. Returns only explicit text matches without
    interpretation or scoring.

    Args:
        text: Raw publication text to analyze

    Returns:
        Dictionary with:
            has_sponsor_signal: bool - True if sponsor patterns detected
            sponsor_names: list[str] - Extracted sponsor names
            company_affiliation_signal: bool - True if affiliation patterns detected
            company_names: list[str] - Extracted company names
            evidence_snippets: list[str] - Up to 2 text excerpts (≤160 chars each)
    """
    if not text or not isinstance(text, str):
        return {
            "has_sponsor_signal": False,
            "sponsor_names": [],
            "company_affiliation_signal": False,
            "company_names": [],
            "evidence_snippets": [],
        }

    sponsor_names = []
    company_names = []
    evidence_snippets = []

    # Extract sponsor signals
    for pattern in SPONSOR_PATTERNS:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            sponsor_name = match.group(1).strip()
            # Clean up common trailing artifacts
            sponsor_name = re.sub(r'[,;.]$', '', sponsor_name)
            if sponsor_name and sponsor_name not in sponsor_names:
                sponsor_names.append(sponsor_name)

                # Extract evidence snippet (≤160 chars, includes trigger phrase)
                if len(evidence_snippets) < 2:
                    start = max(0, match.start() - 20)
                    end = min(len(text), match.end() + 80)
                    snippet = text[start:end].strip()
                    if len(snippet) > 160:
                        snippet = snippet[:157] + "..."
                    if snippet not in evidence_snippets:
                        evidence_snippets.append(snippet)

    # Extract company affiliation signals
    for pattern in COMPANY_PATTERNS:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            company_name = match.group(1).strip()
            # Clean up common trailing artifacts
            company_name = re.sub(r'[,;.]$', '', company_name)
            if company_name and company_name not in company_names:
                company_names.append(company_name)

                # Extract evidence snippet (≤160 chars, includes trigger phrase)
                if len(evidence_snippets) < 2:
                    start = max(0, match.start() - 20)
                    end = min(len(text), match.end() + 80)
                    snippet = text[start:end].strip()
                    if len(snippet) > 160:
                        snippet = snippet[:157] + "..."
                    if snippet not in evidence_snippets:
                        evidence_snippets.append(snippet)

    return {
        "has_sponsor_signal": len(sponsor_names) > 0,
        "sponsor_names": sponsor_names[:5],  # Limit to 5 to avoid noise
        "company_affiliation_signal": len(company_names) > 0,
        "company_names": company_names[:5],  # Limit to 5 to avoid noise
        "evidence_snippets": evidence_snippets[:2],  # Max 2 snippets as specified
    }


def enrich_publication_commercial(
    publication_id: str, text: str, cache_dir: str
) -> dict:
    """Enrich publication with commercial signals, using cache if available.

    Args:
        publication_id: Unique publication identifier
        text: Publication text to analyze
        cache_dir: Directory for caching enrichment results

    Returns:
        Commercial signals dictionary (see extract_commercial_signals)
    """
    cache_path = Path(cache_dir) / f"{publication_id}_commercial.json"

    # Check cache first
    if cache_path.exists():
        try:
            with open(cache_path, "r") as f:
                cached = json.load(f)
                logger.debug("Cache hit for publication %s", publication_id)
                return cached
        except Exception as e:
            logger.warning("Failed to load cache for %s: %s", publication_id, e)

    # Extract signals
    signals = extract_commercial_signals(text)

    # Save to cache
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(signals, f, indent=2)
        logger.debug("Cached commercial signals for publication %s", publication_id)
    except Exception as e:
        logger.warning("Failed to cache signals for %s: %s", publication_id, e)

    return signals
