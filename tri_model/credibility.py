"""Credibility scoring for tri-model pipeline.

This module computes credibility scores for publications in the tri-model pipeline,
using the same LLM-based credibility system as the classic pipeline.
"""

import logging
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Import from existing credibility module
try:
    from mcp_server.llm_credibility import score_credibility as _score_credibility_impl
except ImportError:
    logger.warning("llm_credibility module not available")
    _score_credibility_impl = None


def score_paper_credibility(paper: Dict) -> Dict:
    """Score credibility of a paper using LLM-based credibility system.

    This function adapts the classic pipeline's credibility scoring to work
    with tri-model paper format.

    Args:
        paper: Publication dict with fields:
            - id (publication ID)
            - title (required)
            - source (optional)
            - venue (optional)
            - date/published_date (optional)
            - raw_text or summary (for abstract)
            - url (optional, for citation lookup)

    Returns:
        Dictionary with keys:
            - credibility_score: int 0-100 or None if failed
            - credibility_reason: str explanation
            - credibility_confidence: str "low|medium|high"
            - credibility_signals: dict with metadata
            - scored_at: ISO timestamp
            - scoring_version: version identifier
            - scoring_model: model name used
            - error: optional error message if scoring failed
    """
    if _score_credibility_impl is None:
        logger.warning("Credibility scoring not available (module import failed)")
        return {
            "credibility_score": None,
            "credibility_reason": "Credibility module not available",
            "credibility_confidence": "low",
            "credibility_signals": {},
            "scored_at": datetime.now().isoformat(),
            "scoring_version": "unavailable",
            "scoring_model": "none",
            "error": "Module not available"
        }

    # Adapt paper format to credibility scorer expected format
    item = {
        "id": paper.get("id"),
        "title": paper.get("title", ""),
        "source": paper.get("source", ""),
        "venue": paper.get("venue", ""),
        "published_date": paper.get("date") or paper.get("published_date", ""),
        "raw_text": paper.get("raw_text"),
        "summary": paper.get("summary"),
        "url": paper.get("url", ""),
    }

    try:
        result = _score_credibility_impl(item)
        logger.info(
            "Scored credibility for %s: score=%s",
            paper.get("id", "unknown")[:16],
            result.get("credibility_score")
        )
        return result
    except Exception as e:
        logger.error("Error scoring credibility for %s: %s", paper.get("id", "unknown")[:16], e)
        return {
            "credibility_score": None,
            "credibility_reason": f"Error: {str(e)}",
            "credibility_confidence": "low",
            "credibility_signals": {},
            "scored_at": datetime.now().isoformat(),
            "scoring_version": "error",
            "scoring_model": "none",
            "error": str(e)
        }
