"""LLM-based credibility scoring for must-reads items.

This module provides LLM-driven credibility scoring augmented by citation signals:
- Integrates with existing bibliometrics system (bibliometrics/adapters.py)
- Combines objective citation metrics with LLM-based evidence quality assessment
- Never invents citation data - only uses real retrieved values
- Treats missing citation data as "unknown", not "zero"

Environment variables:
- SPOTITEARLY_LLM_API_KEY: Required API key for LLM calls
- SPOTITEARLY_CRED_MODEL: Model name for credibility (default: use SPOTITEARLY_LLM_MODEL)
- SPOTITEARLY_LLM_MODEL: Fallback model name (default: gpt-4o-mini)
"""

import json
import logging
import os
import re
from typing import Dict, Optional, List
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Version identifier for credibility scoring
CREDIBILITY_VERSION = "poc_v3"

# Default model if not specified
DEFAULT_MODEL = "gpt-4o-mini"

# Preprint detection patterns (for venue/source strings)
PREPRINT_PATTERNS = [
    r"biorxiv",
    r"medrxiv",
    r"arxiv",
    r"preprint",
    r"ssrn",
]

# Scoring rubric prompt for credibility
CREDIBILITY_PROMPT_TEMPLATE = """You are a research credibility analyst for SpotItEarly, a company focused on early cancer detection technologies.

Analyze this publication and score its credibility (0-100) based on ONLY evidence rigor and trustworthiness (NOT relevance to SpotItEarly).

CREDIBILITY RUBRIC:

1. PUBLICATION TYPE (base scoring):
   - Peer-reviewed journal article: 50 points (base)
   - Preprint (bioRxiv/medRxiv): 30 points (CAP at 65 max)
   - Conference paper: 40 points
   - Review/meta-analysis: 45 points (secondary evidence)
   - Correction/erratum: 10 points (major penalty)

2. STUDY DESIGN BOOSTS (additive):
   - Prospective diagnostic accuracy study: +25 points
   - Human cohort study: +15 points
   - External validation cohort: +20 points
   - Multicenter study: +10 points
   - Randomized controlled trial: +20 points
   - Large sample size (>500 patients): +10 points

3. RIGOR PENALTIES:
   - Small sample (<50 patients): -10 points
   - Single-center only: -5 points
   - No external validation: -10 points
   - Industry-funded conflicts: -5 points

4. CITATION SIGNALS (if available):
   - High citations per year for age: +5 points
   - Very low/no citations but paper is new (<90 days): neutral (0 points)
   - Very low/no citations and paper is old (>2 years): -10 points
   - NOTE: If citation_data_available is false, ignore citation scoring entirely

IMPORTANT RULES:
- If this is a preprint (bioRxiv/medRxiv), CAP final score at 65
- If this is a correction/erratum, mark correction_or_erratum=true and score ≤20
- If citation data is missing/unavailable, do NOT penalize - say "Citation data not available yet"
- If paper published within last 90 days, do NOT penalize low citations
- Focus on study design, validation, and evidence quality
- Keep credibility_reason to 1-2 sentences, exec-friendly

PUBLICATION TO ANALYZE:
Title: {title}
Source/Venue: {source} / {venue}
Published: {published_date}
Abstract/Summary: {abstract}

CITATION DATA (if available):
{citation_context}

OUTPUT FORMAT (strict JSON):
{{
  "credibility_score": <integer 0-100>,
  "credibility_reason": "<1-2 sentences explaining the score>",
  "credibility_confidence": "<low|medium|high>",
  "credibility_signals": {{
    "peer_reviewed": <true|false>,
    "preprint": <true|false>,
    "study_type": "<prospective|retrospective|review|meta_analysis|case_series|other>",
    "human_cohort": <true|false>,
    "external_validation": <true|false>,
    "multicenter": <true|false>,
    "review_or_meta": <true|false>,
    "correction_or_erratum": <true|false>,
    "citation_count": <integer or null>,
    "citations_per_year": <float or null>,
    "referenced_by_count": <integer or null>,
    "citation_data_available": <true|false>
  }}
}}

Respond ONLY with valid JSON. Do not include markdown formatting or explanations outside the JSON object."""


def _get_api_key() -> Optional[str]:
    """Get LLM API key from environment variable.

    Returns:
        API key string or None if not set
    """
    return os.environ.get("SPOTITEARLY_LLM_API_KEY")


def _get_model_name() -> str:
    """Get LLM model name from environment variables.

    Prefers SPOTITEARLY_CRED_MODEL, falls back to SPOTITEARLY_LLM_MODEL.

    Returns:
        Model name string (defaults to gpt-4o-mini)
    """
    return os.environ.get(
        "SPOTITEARLY_CRED_MODEL",
        os.environ.get("SPOTITEARLY_LLM_MODEL", DEFAULT_MODEL)
    )


def _is_preprint(source: str, venue: str) -> bool:
    """Detect if publication is a preprint based on source/venue strings.

    Args:
        source: Source name
        venue: Venue name

    Returns:
        True if preprint detected
    """
    combined = f"{source} {venue}".lower()
    for pattern in PREPRINT_PATTERNS:
        if re.search(pattern, combined):
            return True
    return False


def _is_recent_publication(published_date: str, days_threshold: int = 90) -> bool:
    """Check if publication is recent (within threshold days).

    Args:
        published_date: ISO8601 date string
        days_threshold: Number of days to consider "recent" (default: 90)

    Returns:
        True if publication is within threshold days
    """
    if not published_date:
        return False

    try:
        pub_date = datetime.fromisoformat(published_date.replace("Z", "+00:00"))
        if pub_date.tzinfo is None:
            pub_date = pub_date.replace(tzinfo=None)
            now = datetime.now()
        else:
            now = datetime.now().astimezone()

        age_days = (now - pub_date).days
        return age_days <= days_threshold

    except (ValueError, AttributeError):
        return False


def _get_citation_features(item: Dict) -> Optional[Dict]:
    """Retrieve citation features using existing bibliometrics system.

    Integrates with bibliometrics/adapters.py to fetch citation data.
    Caches results to avoid redundant API calls.

    Args:
        item: Must-reads item dictionary with fields:
            - id (publication ID for cache key)
            - url (may contain DOI or PMID)
            - title (for fallback lookup)
            - venue (journal name)

    Returns:
        Dictionary with citation features:
            - citation_count: int or None
            - citations_per_year: float or None
            - referenced_by_count: int or None
            - citation_data_available: bool
        or None if bibliometrics system unavailable
    """
    try:
        from bibliometrics.adapters import enrich_publication, BiblioMetrics
        from bibliometrics.adapters import resolve_ids_to_identifiers
    except ImportError:
        logger.warning("bibliometrics module not available, skipping citation lookup")
        return None

    pub_id = item.get("id", "")
    url = item.get("url", "")
    title = item.get("title", "")

    # Extract DOI/PMID from URL if possible
    doi = None
    pmid = None

    # DOI pattern
    doi_match = re.search(r"10\.\d{4,}/[^\s]+", url)
    if doi_match:
        doi = doi_match.group(0)

    # PMID pattern
    pmid_match = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", url)
    if pmid_match:
        pmid = int(pmid_match.group(1))

    # Check cache first
    cache_dir = Path("data/cache/credibility")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{pub_id}_citation.json"

    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cache_data = json.load(f)

            # Check cache age (7 days TTL)
            cached_at = datetime.fromisoformat(cache_data.get("cached_at", ""))
            age = datetime.now() - cached_at
            if age < timedelta(days=7):
                logger.info("Using cached citation data for pub_id=%s", pub_id)
                return cache_data.get("citation_features")
        except Exception as e:
            logger.warning("Failed to load citation cache for %s: %s", pub_id, e)

    # Try to enrich publication using bibliometrics system
    logger.info("Fetching citation data for pub_id=%s (doi=%s, pmid=%s)", pub_id, doi, pmid)

    try:
        biblio_result = enrich_publication(
            doi=doi,
            pmid=pmid,
            title=title if not (doi or pmid) else None,  # Only use title as fallback
            max_cited_by=0,  # Don't fetch full cited-by list (expensive)
            max_references=0,  # Don't fetch references
            max_related=0  # Don't fetch related papers
        )

        if biblio_result:
            citation_features = {
                "citation_count": biblio_result.citation_count,
                "citations_per_year": biblio_result.citations_per_year,
                "referenced_by_count": biblio_result.citation_count,  # Same as citation_count
                "citation_data_available": True
            }

            logger.info("Retrieved citation data: count=%s, per_year=%s",
                       biblio_result.citation_count,
                       biblio_result.citations_per_year)
        else:
            logger.info("No citation data available for pub_id=%s", pub_id)
            citation_features = {
                "citation_count": None,
                "citations_per_year": None,
                "referenced_by_count": None,
                "citation_data_available": False
            }

        # Cache the result
        cache_data = {
            "citation_features": citation_features,
            "cached_at": datetime.now().isoformat()
        }
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2)

        return citation_features

    except Exception as e:
        logger.error("Error fetching citation data for pub_id=%s: %s", pub_id, e)
        return {
            "citation_count": None,
            "citations_per_year": None,
            "referenced_by_count": None,
            "citation_data_available": False
        }


def _build_citation_context(citation_features: Optional[Dict], published_date: str) -> str:
    """Build citation context string for LLM prompt.

    Args:
        citation_features: Citation features dict or None
        published_date: Publication date string

    Returns:
        Human-readable citation context string
    """
    if not citation_features or not citation_features.get("citation_data_available"):
        return "Citation data: Not available"

    citation_count = citation_features.get("citation_count")
    citations_per_year = citation_features.get("citations_per_year")

    parts = []
    if citation_count is not None:
        parts.append(f"Citation count: {citation_count}")
    if citations_per_year is not None:
        parts.append(f"Citations per year: {citations_per_year:.1f}")

    # Add age context
    if _is_recent_publication(published_date, days_threshold=90):
        parts.append("(Paper is very recent - <90 days old)")

    return "Citation data: " + ", ".join(parts) if parts else "Citation data: Available but incomplete"


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
                    "content": "You are a research credibility analyst. Respond only with valid JSON."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.3,  # Lower temperature for more consistent scoring
            max_tokens=600,
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
            lines = text.split("\n")
            lines = lines[1:]  # Remove first line (```json or ```)
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]  # Remove last line (```)
            text = "\n".join(lines)

        data = json.loads(text)

        # Validate required fields
        required_fields = ["credibility_score", "credibility_reason", "credibility_confidence", "credibility_signals"]
        if not all(field in data for field in required_fields):
            logger.warning("LLM response missing required fields: %s", data.keys())
            return None

        # Validate types
        if not isinstance(data["credibility_score"], int):
            logger.warning("credibility_score is not an integer: %s", type(data["credibility_score"]))
            return None

        if not isinstance(data["credibility_reason"], str):
            logger.warning("credibility_reason is not a string: %s", type(data["credibility_reason"]))
            return None

        if data["credibility_confidence"] not in ["low", "medium", "high"]:
            logger.warning("Invalid credibility_confidence value: %s", data["credibility_confidence"])
            return None

        # Validate score range
        if not (0 <= data["credibility_score"] <= 100):
            logger.warning("credibility_score out of range: %s", data["credibility_score"])
            return None

        # Validate signals structure
        signals = data["credibility_signals"]
        if not isinstance(signals, dict):
            logger.warning("credibility_signals is not a dict: %s", type(signals))
            return None

        return data

    except json.JSONDecodeError as e:
        logger.warning("Failed to parse LLM response as JSON: %s", e)
        return None
    except Exception as e:
        logger.warning("Unexpected error parsing LLM response: %s", e)
        return None


def score_credibility(item: Dict, citation_features: Optional[Dict] = None) -> Dict:
    """Score credibility of a must-reads item using LLM + citation signals.

    Args:
        item: Must-reads item dictionary with fields:
            - title (required)
            - source (optional)
            - venue (optional)
            - published_date (optional)
            - raw_text or summary (for abstract)
            - credibility_score (optional, for caching check)
            - scoring_version (optional, for caching check)
        citation_features: Optional pre-fetched citation features dict
            (if None, will attempt to fetch using bibliometrics system)

    Returns:
        Dictionary with keys:
            - credibility_score: int 0-100 or None if failed
            - credibility_reason: str explanation or empty if failed
            - credibility_confidence: str "low|medium|high" or "low" if failed
            - credibility_signals: dict with peer_reviewed, preprint, study_type, etc.
            - scored_at: ISO timestamp
            - scoring_version: "poc_v3"
            - scoring_model: model name used
            - error: optional error message if scoring failed
    """
    # Check cache: if already scored with poc_v3 and has valid score, return cached
    if (item.get("scoring_version") == CREDIBILITY_VERSION and
        item.get("credibility_score") is not None):
        logger.info("Using cached credibility score for item: %s", item.get("id", "unknown"))
        return {
            "credibility_score": item["credibility_score"],
            "credibility_reason": item.get("credibility_reason", ""),
            "credibility_confidence": item.get("credibility_confidence", "medium"),
            "credibility_signals": item.get("credibility_signals", {}),
            "scored_at": item.get("scored_at", datetime.now().isoformat()),
            "scoring_version": CREDIBILITY_VERSION,
            "scoring_model": item.get("scoring_model", "cached"),
        }

    # Get API key
    api_key = _get_api_key()
    if not api_key:
        logger.warning("SPOTITEARLY_LLM_API_KEY not set, cannot score credibility")
        return {
            "credibility_score": None,
            "credibility_reason": "",
            "credibility_confidence": "low",
            "credibility_signals": {},
            "scored_at": datetime.now().isoformat(),
            "scoring_version": CREDIBILITY_VERSION,
            "scoring_model": "none",
            "error": "API key not configured"
        }

    # Get model name
    model = _get_model_name()

    # Extract fields
    title = item.get("title", "")
    source = item.get("source", "")
    venue = item.get("venue", "")
    published_date = item.get("published_date", "")
    abstract = item.get("raw_text") or item.get("summary") or ""

    if not title:
        logger.warning("Item missing title, cannot score credibility")
        return {
            "credibility_score": None,
            "credibility_reason": "",
            "credibility_confidence": "low",
            "credibility_signals": {},
            "scored_at": datetime.now().isoformat(),
            "scoring_version": CREDIBILITY_VERSION,
            "scoring_model": model,
            "error": "Missing title"
        }

    # Fetch citation features if not provided
    if citation_features is None:
        citation_features = _get_citation_features(item)

    # Build citation context for LLM
    citation_context = _build_citation_context(citation_features, published_date)

    # Build prompt
    prompt = CREDIBILITY_PROMPT_TEMPLATE.format(
        title=title,
        source=source,
        venue=venue,
        published_date=published_date,
        abstract=abstract[:2000],  # Truncate to avoid token limits
        citation_context=citation_context
    )

    # Call LLM with retry logic
    max_retries = 2
    parsed_result = None

    for attempt in range(max_retries):
        logger.info("Scoring credibility (attempt %d/%d) for: %s", attempt + 1, max_retries, title[:80])

        response_text = _call_llm(prompt, api_key, model)
        if not response_text:
            logger.warning("LLM call failed on attempt %d", attempt + 1)
            continue

        parsed_result = _parse_llm_response(response_text)
        if parsed_result:
            logger.info("Successfully scored credibility: %s (score=%d)",
                       title[:80], parsed_result["credibility_score"])
            break
        else:
            logger.warning("Failed to parse LLM response on attempt %d: %s",
                          attempt + 1, response_text[:200])

    # Return result or fallback
    if parsed_result:
        # Apply preprint cap if detected
        is_preprint = _is_preprint(source, venue)
        if is_preprint and parsed_result["credibility_score"] > 65:
            logger.info("Applying preprint cap (65) to score %d for: %s",
                       parsed_result["credibility_score"], title[:80])
            parsed_result["credibility_score"] = 65

        return {
            "credibility_score": parsed_result["credibility_score"],
            "credibility_reason": parsed_result["credibility_reason"],
            "credibility_confidence": parsed_result["credibility_confidence"],
            "credibility_signals": parsed_result["credibility_signals"],
            "scored_at": datetime.now().isoformat(),
            "scoring_version": CREDIBILITY_VERSION,
            "scoring_model": model,
        }
    else:
        logger.error("Failed to score credibility after %d attempts: %s", max_retries, title[:80])
        return {
            "credibility_score": None,
            "credibility_reason": "",
            "credibility_confidence": "low",
            "credibility_signals": {},
            "scored_at": datetime.now().isoformat(),
            "scoring_version": CREDIBILITY_VERSION,
            "scoring_model": model,
            "error": "LLM scoring failed after retries"
        }


def batch_score_credibility(items: List[Dict], use_cache: bool = True) -> List[Dict]:
    """Score credibility for a batch of items.

    Args:
        items: List of must-reads item dictionaries
        use_cache: Whether to use cached scores (default: True)

    Returns:
        List of scoring results (same order as input)
    """
    results = []

    for item in items:
        # Optionally skip cached items
        if (not use_cache or
            item.get("scoring_version") != CREDIBILITY_VERSION or
            item.get("credibility_score") is None):
            result = score_credibility(item)
        else:
            # Return cached result
            result = {
                "credibility_score": item["credibility_score"],
                "credibility_reason": item.get("credibility_reason", ""),
                "credibility_confidence": item.get("credibility_confidence", "medium"),
                "credibility_signals": item.get("credibility_signals", {}),
                "scored_at": item.get("scored_at", datetime.now().isoformat()),
                "scoring_version": CREDIBILITY_VERSION,
                "scoring_model": item.get("scoring_model", "cached"),
            }

        results.append(result)

    return results
