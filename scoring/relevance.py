"""Relevance scoring wrapper that uses the LLM relevancy scoring from mcp_server.

This module provides a thin wrapper around the MCP server's LLM relevancy scoring
to maintain backward compatibility with the scoring.relevance import path.
"""

import logging
from typing import Dict

logger = logging.getLogger(__name__)

try:
    from mcp_server.llm_relevancy import compute_relevancy_score as _llm_compute_relevancy
except ImportError:
    logger.warning("LLM relevancy module not available")
    _llm_compute_relevancy = None


def compute_relevance_score(
    title: str,
    abstract: str,
    source: str = "",
) -> Dict:
    """Compute relevance score for a publication.

    Args:
        title: Publication title
        abstract: Abstract or summary text
        source: Source name

    Returns:
        Dictionary with keys:
        - score: Relevance score (0-100)
        - reason: Explanation of the score
        - matched_keywords: List of matched keywords (empty if using LLM)
    """
    if _llm_compute_relevancy is None:
        logger.warning("LLM relevancy scoring not available, returning default score of 0")
        return {
            "score": 0,
            "reason": "LLM scoring module not available",
            "matched_keywords": [],
        }

    try:
        result = _llm_compute_relevancy(
            title=title,
            abstract=abstract,
            source=source,
        )

        # Map LLM response to expected format
        return {
            "score": result.get("relevancy_score", 0),
            "reason": result.get("relevancy_reason", ""),
            "matched_keywords": [],  # LLM-based scoring doesn't use keyword matching
        }
    except Exception as e:
        logger.error("Error computing relevance score: %s", e)
        return {
            "score": 0,
            "reason": f"Error: {str(e)}",
            "matched_keywords": [],
        }
