"""Relevance scoring wrapper that uses the LLM relevancy scoring from mcp_server.

This module provides a thin wrapper around the MCP server's LLM relevancy scoring
to maintain backward compatibility with the scoring.relevance import path.
"""

import logging
import os
from typing import Dict

logger = logging.getLogger(__name__)

# Module-level flag to track if we've logged the unavailability warning
_logged_unavailable_warning = False

try:
    from mcp_server.llm_relevancy import compute_relevancy_score as _llm_compute_relevancy
    _import_succeeded = True
except ImportError as e:
    logger.warning("LLM relevancy module not available: %s", e)
    _llm_compute_relevancy = None
    _import_succeeded = False


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
        - score: Relevance score (0-100) or None if LLM unavailable
        - reason: Explanation of the score
        - matched_keywords: List of matched keywords (empty if using LLM)
    """
    global _logged_unavailable_warning

    if _llm_compute_relevancy is None:
        # Log warning only once per process, not per item
        if not _logged_unavailable_warning:
            api_key = os.getenv("SPOTITEARLY_LLM_API_KEY")
            if not api_key:
                logger.warning(
                    "LLM relevancy scoring disabled: SPOTITEARLY_LLM_API_KEY environment variable not set. "
                    "Set this to enable LLM-based relevancy scoring for publications."
                )
            elif not _import_succeeded:
                logger.warning(
                    "LLM relevancy scoring disabled: Import failed (see earlier warning). "
                    "Returning None for all relevance scores."
                )
            _logged_unavailable_warning = True

        return {
            "score": None,  # None indicates "not scored", different from 0 (irrelevant)
            "reason": "LLM scoring not configured",
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
            "score": None,
            "reason": f"Error: {str(e)}",
            "matched_keywords": [],
        }
