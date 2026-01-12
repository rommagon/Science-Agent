"""Credibility scoring wrapper that uses the LLM credibility scoring from mcp_server.

This module provides a thin wrapper around the MCP server's LLM credibility scoring
to maintain backward compatibility with the scoring.credibility import path.
"""

import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

try:
    from mcp_server.llm_credibility import compute_credibility_score as _llm_compute_credibility
except ImportError:
    logger.warning("LLM credibility module not available")
    _llm_compute_credibility = None


def compute_credibility_score(
    biblio_metrics: Optional[Any],
    title: str,
    abstract: str,
    has_sponsor_signal: bool = False,
    sponsor_names: List[str] = None,
) -> Dict:
    """Compute credibility score for a publication.

    Args:
        biblio_metrics: Bibliometric data (citation count, venue, etc.)
        title: Publication title
        abstract: Abstract or summary text
        has_sponsor_signal: Whether commercial sponsorship was detected
        sponsor_names: List of sponsor names

    Returns:
        Dictionary with keys:
        - score: Credibility score (0-100)
        - components: Breakdown of score components
        - reason: Explanation of the score
    """
    if _llm_compute_credibility is None:
        logger.warning("LLM credibility scoring not available, returning default score of 0")
        return {
            "score": 0,
            "components": {},
            "reason": "LLM scoring module not available",
        }

    try:
        # Prepare bibliometric context for LLM
        biblio_context = ""
        if biblio_metrics:
            parts = []
            if hasattr(biblio_metrics, 'citation_count') and biblio_metrics.citation_count:
                parts.append(f"Citations: {biblio_metrics.citation_count}")
            if hasattr(biblio_metrics, 'venue_name') and biblio_metrics.venue_name:
                parts.append(f"Venue: {biblio_metrics.venue_name}")
            if hasattr(biblio_metrics, 'pub_type') and biblio_metrics.pub_type:
                parts.append(f"Type: {biblio_metrics.pub_type}")
            if parts:
                biblio_context = "; ".join(parts)

        result = _llm_compute_credibility(
            title=title,
            abstract=abstract,
            biblio_context=biblio_context,
            has_sponsor_signal=has_sponsor_signal,
            sponsor_names=sponsor_names or [],
        )

        # Map LLM response to expected format
        return {
            "score": result.get("credibility_score", 0),
            "components": result.get("components", {}),
            "reason": result.get("credibility_reason", ""),
        }
    except Exception as e:
        logger.error("Error computing credibility score: %s", e)
        return {
            "score": 0,
            "components": {},
            "reason": f"Error: {str(e)}",
        }
