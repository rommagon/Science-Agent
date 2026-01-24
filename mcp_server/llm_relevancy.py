"""LLM-based relevancy scoring for must-reads items.

This module provides LLM-driven relevancy scoring with SpotItEarly's cancer detection rubric:
- Breast cancer (highest priority), lung, colon, then other cancers
- Breath collection/VOC/breathomics (major boost regardless of cancer type)
- Sensor/animal-model detection (high relevance)
- Biopsy/NGS/genomics (second-tier relevance)
- Non-cancer topics penalized heavily

Environment variables:
- SPOTITEARLY_LLM_API_KEY: Required API key for LLM calls
- SPOTITEARLY_LLM_MODEL: Model name (default: gpt-4o-mini)
"""

import hashlib
import json
import logging
import os
import time
from typing import Dict, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

# Module-level cache for scoring results within a run
# Key: (run_id, publication_id) -> scoring result dict
_RUN_CACHE: Dict[Tuple[str, str], Dict] = {}

# Version identifier for this scoring implementation
SCORING_VERSION = "poc_v2"

# Default model if not specified
DEFAULT_MODEL = "gpt-4o-mini"

# Scoring rubric prompt
RELEVANCY_PROMPT_TEMPLATE = """You are a research relevance analyst for SpotItEarly, a company focused on early cancer detection technologies.

Analyze this publication and score its relevance (0-100) based on the following rubric:

PRIORITY HIERARCHY:
1. CANCER TYPE PRIORITY (base scoring):
   - Breast cancer: 40 points (highest priority)
   - Lung cancer: 35 points
   - Colon cancer: 30 points
   - Other cancers: 20 points
   - Non-cancer topics: 0 points (heavy penalty)

2. DETECTION METHOD BOOSTS (additive):
   - Breath collection/VOC/breathomics: +40 points (MAJOR BOOST, even for non-top-3 cancers)
   - Sensor-based detection: +20 points
   - Animal model detection: +20 points
   - Biopsy/NGS/genomics: +10 points (second-tier)
   - Early detection/screening focus: +10 points

3. RELEVANCE FACTORS (penalties):
   - Treatment-only (no detection): -20 points
   - Review/meta-analysis (no novel method): -10 points
   - Purely computational/database: -15 points

SCORING GUIDELINES:
- Maximum score: 100
- Minimum score: 0
- Be conservative: only score >80 for highly relevant breakthrough methods
- Score 60-79 for solid relevance to early detection
- Score 40-59 for moderate relevance
- Score 20-39 for weak relevance
- Score 0-19 for irrelevant or non-cancer topics

PUBLICATION TO ANALYZE:
Title: {title}
Source: {source}
Abstract/Summary: {abstract}

OUTPUT FORMAT (strict JSON):
{{
  "relevancy_score": <integer 0-100>,
  "relevancy_reason": "<1-3 sentences explaining the score>",
  "confidence": "<low|medium|high>",
  "signals": {{
    "cancer_type": "<breast|lung|colon|other|none>",
    "breath_based": <true|false>,
    "animal_model": <true|false>,
    "ngs_genomics": <true|false>
  }}
}}

Respond ONLY with valid JSON. Do not include markdown formatting or explanations outside the JSON object."""


def _compute_input_fingerprint(title: str, abstract: str) -> str:
    """Compute a stable hash of the input for deduplication.

    Args:
        title: Publication title
        abstract: Abstract text

    Returns:
        SHA256 hex digest of normalized input
    """
    normalized = f"{title.strip().lower()}||{abstract.strip().lower()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def init_run_cache(run_id: str, db_path: str = "data/db/acitrack.db") -> int:
    """Initialize the run cache by loading existing scores from database.

    Args:
        run_id: Run identifier to load scores for
        db_path: Path to database file

    Returns:
        Number of scores loaded from database
    """
    global _RUN_CACHE

    try:
        from storage.sqlite_store import get_relevancy_scores_for_run

        scores = get_relevancy_scores_for_run(run_id, db_path)

        # Populate cache
        for pub_id, result in scores.items():
            cache_key = (run_id, pub_id)
            _RUN_CACHE[cache_key] = result

        logger.info("Loaded %d relevancy scores from DB for run_id=%s", len(scores), run_id)
        return len(scores)

    except Exception as e:
        logger.warning("Failed to initialize run cache: %s", e)
        return 0


def clear_run_cache() -> None:
    """Clear the run cache (useful for testing)."""
    global _RUN_CACHE
    _RUN_CACHE.clear()
    logger.debug("Cleared relevancy scoring run cache")


def _get_api_key() -> Optional[str]:
    """Get LLM API key from environment variable.

    Returns:
        API key string or None if not set
    """
    return os.environ.get("SPOTITEARLY_LLM_API_KEY")


def _get_model_name() -> str:
    """Get LLM model name from environment variable.

    Returns:
        Model name string (defaults to gpt-4o-mini)
    """
    return os.environ.get("SPOTITEARLY_LLM_MODEL", DEFAULT_MODEL)


def _call_llm(prompt: str, api_key: str, model: str) -> Optional[str]:
    """Call OpenAI-compatible LLM API.

    Args:
        prompt: The prompt to send
        api_key: API key for authentication
        model: Model name to use

    Returns:
        Raw response text or None on error
    """
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)

        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a research relevance analyst. Respond only with valid JSON."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.3,
            max_completion_tokens=500,
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error("LLM API call failed: %s", e)
        return None


def _parse_llm_response(response_text: str) -> Optional[Dict]:
    """Parse and validate LLM JSON response.

    Args:
        response_text: Raw LLM response

    Returns:
        Parsed dict or None if invalid
    """
    if not response_text:
        return None

    try:
        # Remove markdown code fences if present
        text = response_text.strip()
        if text.startswith("```"):
            # Extract JSON from code block
            lines = text.split("\n")
            # Remove first line (```json or ```)
            lines = lines[1:]
            # Remove last line (```)
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        data = json.loads(text)

        # Validate required fields
        required_fields = ["relevancy_score", "relevancy_reason", "confidence", "signals"]
        if not all(field in data for field in required_fields):
            logger.warning("LLM response missing required fields: %s", data.keys())
            return None

        # Validate types
        if not isinstance(data["relevancy_score"], int):
            logger.warning("relevancy_score is not an integer: %s", type(data["relevancy_score"]))
            return None

        if not isinstance(data["relevancy_reason"], str):
            logger.warning("relevancy_reason is not a string: %s", type(data["relevancy_reason"]))
            return None

        if data["confidence"] not in ["low", "medium", "high"]:
            logger.warning("Invalid confidence value: %s", data["confidence"])
            return None

        # Validate score range
        if not (0 <= data["relevancy_score"] <= 100):
            logger.warning("relevancy_score out of range: %s", data["relevancy_score"])
            return None

        # Validate signals structure
        signals = data["signals"]
        if not isinstance(signals, dict):
            logger.warning("signals is not a dict: %s", type(signals))
            return None

        return data

    except json.JSONDecodeError as e:
        logger.warning("Failed to parse LLM response as JSON: %s", e)
        return None
    except Exception as e:
        logger.warning("Unexpected error parsing LLM response: %s", e)
        return None


def score_relevancy(
    item: Dict,
    run_id: Optional[str] = None,
    mode: Optional[str] = None,
    store_to_db: bool = True,
    db_path: str = "data/db/acitrack.db",
) -> Dict:
    """Score relevancy of a must-reads item using LLM.

    Args:
        item: Must-reads item dictionary with fields:
            - id (required for caching)
            - title (required)
            - raw_text or summary (for abstract)
            - source (optional)
            - relevancy_score (optional, for caching check)
            - scoring_version (optional, for caching check)
        run_id: Optional run identifier for caching (e.g., "daily-2026-01-20")
        mode: Optional run mode ("daily" or "weekly") for DB storage
        store_to_db: Whether to store result to database (default: True)
        db_path: Path to database file

    Returns:
        Dictionary with keys:
            - relevancy_score: int 0-100 or None if failed
            - relevancy_reason: str explanation or empty if failed
            - confidence: str "low|medium|high" or "low" if failed
            - signals: dict with cancer_type, breath_based, animal_model, ngs_genomics
            - scored_at: ISO timestamp
            - scoring_version: "poc_v2"
            - scoring_model: model name used
            - error: optional error message if scoring failed
    """
    global _RUN_CACHE

    pub_id = item.get("id", "")

    # Check run cache first (highest priority)
    if run_id and pub_id:
        cache_key = (run_id, pub_id)
        if cache_key in _RUN_CACHE:
            logger.debug("Using run cache for pub_id=%s", pub_id)
            return _RUN_CACHE[cache_key]

    # Check item cache: if already scored with poc_v2 and has valid score, return cached
    if (item.get("scoring_version") == SCORING_VERSION and
        item.get("relevancy_score") is not None):
        logger.debug("Using item cache for pub_id=%s", pub_id)
        result = {
            "relevancy_score": item["relevancy_score"],
            "relevancy_reason": item.get("relevancy_reason", ""),
            "confidence": item.get("confidence", "medium"),
            "signals": item.get("signals", {}),
            "scored_at": item.get("scored_at", datetime.now().isoformat()),
            "scoring_version": SCORING_VERSION,
            "scoring_model": item.get("scoring_model", "cached"),
        }

        # Store to run cache if run_id provided
        if run_id and pub_id:
            _RUN_CACHE[(run_id, pub_id)] = result

        return result

    # Get API key
    api_key = _get_api_key()
    if not api_key:
        logger.warning("SPOTITEARLY_LLM_API_KEY not set, cannot score relevancy")
        return {
            "relevancy_score": None,
            "relevancy_reason": "",
            "confidence": "low",
            "signals": {},
            "scored_at": datetime.now().isoformat(),
            "scoring_version": SCORING_VERSION,
            "scoring_model": "none",
            "error": "API key not configured"
        }

    # Get model name
    model = _get_model_name()

    # Extract fields
    title = item.get("title", "")
    abstract = item.get("raw_text") or item.get("summary") or item.get("one_liner") or ""
    source = item.get("source", "")

    if not title:
        logger.warning("Item missing title, cannot score relevancy")
        return {
            "relevancy_score": None,
            "relevancy_reason": "",
            "confidence": "low",
            "signals": {},
            "scored_at": datetime.now().isoformat(),
            "scoring_version": SCORING_VERSION,
            "scoring_model": model,
            "error": "Missing title"
        }

    # Build prompt
    prompt = RELEVANCY_PROMPT_TEMPLATE.format(
        title=title,
        source=source,
        abstract=abstract[:2000]  # Truncate to avoid token limits
    )

    # Call LLM with retry logic
    max_retries = 2
    parsed_result = None
    start_time = time.time()
    raw_response_text = None

    for attempt in range(max_retries):
        logger.info("Scoring relevancy (attempt %d/%d) for: %s", attempt + 1, max_retries, title[:80])

        response_text = _call_llm(prompt, api_key, model)
        if not response_text:
            logger.warning("LLM call failed on attempt %d", attempt + 1)
            continue

        raw_response_text = response_text
        parsed_result = _parse_llm_response(response_text)
        if parsed_result:
            logger.info("Successfully scored item: %s (score=%d)",
                       title[:80], parsed_result["relevancy_score"])
            break
        else:
            logger.warning("Failed to parse LLM response on attempt %d: %s",
                          attempt + 1, response_text[:200])

    # Calculate latency
    latency_ms = int((time.time() - start_time) * 1000)

    # Compute input fingerprint
    input_fingerprint = _compute_input_fingerprint(title, abstract)

    # Build result
    if parsed_result:
        result = {
            "relevancy_score": parsed_result["relevancy_score"],
            "relevancy_reason": parsed_result["relevancy_reason"],
            "confidence": parsed_result["confidence"],
            "signals": parsed_result["signals"],
            "scored_at": datetime.now().isoformat(),
            "scoring_version": SCORING_VERSION,
            "scoring_model": model,
        }
    else:
        logger.error("Failed to score item after %d attempts: %s", max_retries, title[:80])
        result = {
            "relevancy_score": None,
            "relevancy_reason": "",
            "confidence": "low",
            "signals": {},
            "scored_at": datetime.now().isoformat(),
            "scoring_version": SCORING_VERSION,
            "scoring_model": model,
            "error": "LLM scoring failed after retries"
        }

    # Store to database if requested
    if store_to_db and run_id and pub_id and mode:
        try:
            from storage.sqlite_store import store_relevancy_scoring_event

            store_relevancy_scoring_event(
                run_id=run_id,
                mode=mode,
                publication_id=pub_id,
                source=source,
                prompt_version=SCORING_VERSION,
                model=model,
                relevancy_score=result["relevancy_score"],
                relevancy_reason=result["relevancy_reason"],
                confidence=result["confidence"],
                signals=result["signals"],
                input_fingerprint=input_fingerprint,
                raw_response={"text": raw_response_text} if raw_response_text else None,
                latency_ms=latency_ms,
                cost_usd=None,  # TODO: track cost if available
                db_path=db_path,
            )
        except Exception as e:
            logger.warning("Failed to store relevancy event to DB: %s", e)

    # Store to run cache if run_id provided
    if run_id and pub_id:
        _RUN_CACHE[(run_id, pub_id)] = result

    return result


def batch_score_relevancy(
    items: list,
    use_cache: bool = True,
    run_id: Optional[str] = None,
    mode: Optional[str] = None,
    store_to_db: bool = True,
    db_path: str = "data/db/acitrack.db",
) -> list:
    """Score relevancy for a batch of items.

    Args:
        items: List of must-reads item dictionaries
        use_cache: Whether to use cached scores (default: True)
        run_id: Optional run identifier for caching
        mode: Optional run mode for DB storage
        store_to_db: Whether to store results to database
        db_path: Path to database file

    Returns:
        List of scoring results (same order as input)
    """
    results = []

    for item in items:
        result = score_relevancy(
            item,
            run_id=run_id,
            mode=mode,
            store_to_db=store_to_db,
            db_path=db_path,
        )
        results.append(result)

    return results


def compute_relevancy_score(
    title: str,
    abstract: str,
    source: str = "",
    pub_id: Optional[str] = None,
    run_id: Optional[str] = None,
    mode: Optional[str] = None,
    store_to_db: bool = True,
    db_path: str = "data/db/acitrack.db",
) -> Dict:
    """Compute relevancy score for a publication (wrapper for backward compatibility).

    This function provides a simplified interface compatible with the scoring.relevance
    module's expectations.

    Args:
        title: Publication title
        abstract: Abstract or summary text
        source: Source name (optional)
        pub_id: Publication ID (optional, for caching)
        run_id: Run identifier (optional, for caching)
        mode: Run mode (optional, for DB storage)
        store_to_db: Whether to store to database
        db_path: Path to database file

    Returns:
        Dictionary with keys:
        - relevancy_score: int 0-100 or None if failed
        - relevancy_reason: str explanation
        - confidence: str "low|medium|high"
        - signals: dict with detection signals
    """
    # Build item dict for score_relevancy
    item = {
        "id": pub_id,
        "title": title,
        "raw_text": abstract,
        "summary": abstract,
        "source": source,
    }

    result = score_relevancy(
        item,
        run_id=run_id,
        mode=mode,
        store_to_db=store_to_db,
        db_path=db_path,
    )

    return result
