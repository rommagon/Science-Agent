"""Configuration for tri-model mini-daily experimental run.

This module provides configuration for the experimental tri-model review system:
- Claude reviews papers
- Gemini reviews papers
- GPT evaluates and produces final decision

Environment Variables:
- TRI_MODEL_MINI_DAILY: Enable tri-model mini-daily run (default: false)
- CLAUDE_API_KEY: Anthropic Claude API key
- CLAUDE_MODEL: Claude model name (default: claude-sonnet-4-5-20250929)
- GEMINI_API_KEY: Google Gemini API key
- GEMINI_MODEL: Gemini model name (default: gemini-2.0-flash-exp)
- MINI_DAILY_WINDOW_HOURS: Lookback window in hours (default: 6)
- MINI_DAILY_MAX_PAPERS: Maximum papers to review (default: 10)
"""

import os
from typing import Optional

# Feature flag
ENABLE_TRI_MODEL_MINI_DAILY = os.getenv("TRI_MODEL_MINI_DAILY", "false").lower() == "true"

# API Keys
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Model names
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-exp")

# Mini-daily parameters
MINI_DAILY_WINDOW_HOURS = int(os.getenv("MINI_DAILY_WINDOW_HOURS", "6"))
MINI_DAILY_MAX_PAPERS = int(os.getenv("MINI_DAILY_MAX_PAPERS", "10"))

# Prompt versions
CLAUDE_REVIEW_VERSION = "v1"
GEMINI_REVIEW_VERSION = "v1"
GPT_EVALUATOR_VERSION = "v1"

# Timeouts and retries
REVIEW_TIMEOUT_SECONDS = 30
MAX_REVIEW_RETRIES = 2


def is_tri_model_enabled() -> bool:
    """Check if tri-model mini-daily is enabled.

    Returns:
        True if enabled and API keys are available
    """
    if not ENABLE_TRI_MODEL_MINI_DAILY:
        return False

    # At least one of Claude or Gemini must be available
    has_claude = CLAUDE_API_KEY is not None
    has_gemini = GEMINI_API_KEY is not None

    return has_claude or has_gemini


def get_available_reviewers() -> list[str]:
    """Get list of available reviewers based on API keys.

    Returns:
        List of available reviewer names: ['claude', 'gemini']
    """
    reviewers = []

    if CLAUDE_API_KEY:
        reviewers.append('claude')

    if GEMINI_API_KEY:
        reviewers.append('gemini')

    return reviewers


def normalize_validation_result(result) -> dict:
    """Normalize validation result to dict format.

    Handles both legacy tuple format and new dict format for backwards compatibility.

    Args:
        result: Either a tuple (bool, str) or dict {"valid": bool, "errors": list, ...}

    Returns:
        Normalized dict with keys: valid, errors, details
    """
    # If already a dict, return as-is (or normalize if missing keys)
    if isinstance(result, dict):
        return {
            "valid": result.get("valid", False),
            "errors": result.get("errors", []),
            "details": result.get("details"),
        }

    # If tuple, convert to dict
    if isinstance(result, tuple):
        is_valid, error_message = result
        if is_valid:
            return {"valid": True, "errors": [], "details": None}
        else:
            return {
                "valid": False,
                "errors": [error_message] if error_message else [],
                "details": error_message,
            }

    # Fallback for unexpected types
    return {"valid": False, "errors": ["Invalid validation result type"], "details": None}


def validate_config() -> dict:
    """Validate tri-model configuration.

    Returns:
        Dictionary with validation result:
        {
            "valid": bool,
            "errors": list[str],  # Empty list if valid
            "details": str or None  # Human-readable summary
        }
    """
    if not ENABLE_TRI_MODEL_MINI_DAILY:
        return {"valid": True, "errors": [], "details": None}

    reviewers = get_available_reviewers()
    errors = []

    if not reviewers:
        errors.append("No reviewer API keys configured (need CLAUDE_API_KEY or GEMINI_API_KEY)")

    # Check that we have OpenAI key for evaluator
    openai_key = os.getenv("SPOTITEARLY_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not openai_key:
        errors.append("No OpenAI API key for GPT evaluator (need SPOTITEARLY_LLM_API_KEY or OPENAI_API_KEY)")

    if errors:
        return {
            "valid": False,
            "errors": errors,
            "details": f"{len(errors)} configuration error(s) found"
        }

    return {"valid": True, "errors": [], "details": None}
