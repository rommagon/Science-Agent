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
import re
import time
from typing import Dict, Optional, Tuple, Set
from datetime import datetime

logger = logging.getLogger(__name__)

# Module-level cache for scoring results within a run
# Key: (run_id, publication_id) -> scoring result dict
_RUN_CACHE: Dict[Tuple[str, str], Dict] = {}

# Version identifier for this scoring implementation
SCORING_VERSION = "poc_v3"

# Default model if not specified
DEFAULT_MODEL = "gpt-4o-mini"

# Default target cancers for hard-constraint layer (can be overridden via env var).
DEFAULT_TARGET_CANCER_TYPES = ("breast", "lung", "prostate", "colon")

# Heuristic keyword sets for post-LLM rule enforcement.
DETECTION_KEYWORDS = (
    "early detection", "early diagnosis", "screening", "screen", "diagnostic",
    "diagnosis", "mced", "multi-cancer early detection", "liquid biopsy",
    "ctdna", "cfdna", "cell-free dna", "methylation", "biomarker",
    "breath", "voc", "urine", "sensor", "electronic nose", "e-nose",
    "sensitivity", "specificity", "auc", "predictive value",
    "psa", "prostate-specific antigen", "colorectal", "colonoscopy",
    "canine", "olfaction", "cancer-sniffing"
)

GENERIC_AI_KEYWORDS = (
    "artificial intelligence", "ai", "machine learning", "deep learning",
    "neural network", "llm", "large language model"
)

MARKET_KEYWORDS = (
    "market", "market size", "market growth", "market forecast", "competitive landscape",
    "investor", "funding", "valuation", "commercial strategy", "go-to-market", "gtm"
)

GENOMICS_KEYWORDS = (
    "genomics", "multi-omics", "omics", "transcriptomics", "proteomics", "epigenomics",
    "genomic profiling", "single-cell", "rna-seq", "whole genome", "wgs", "wes", "ngs"
)

TREATMENT_KEYWORDS = (
    "neoadjuvant", "adjuvant", "chemotherapy", "immunotherapy", "targeted therapy",
    "treatment response", "predicting the efficacy", "pathological response",
    "overall survival", "progression-free survival", "metastatic", "advanced stage",
    "stage iv", "drug resistance", "refractory", "relapsed", "combination therapy"
)

BASIC_BIOLOGY_KEYWORDS = (
    "single-cell atlas", "fibroblast phenotypes", "tumor microenvironment",
    "mechanistic", "pathway", "signaling pathway", "cell lines", "xenograft",
    "murine model", "molecular mechanism", "transcriptomic landscape"
)

SCREENING_IMPACT_KEYWORDS = (
    "population health impact", "complement existing screening", "population-level screening",
    "asymptomatic population", "screening program", "public health impact",
    "multi-cancer early detection", "mced", "genomic blood test", "blood-based screening"
)

BREATH_MODALITY_KEYWORDS = (
    "breath", "exhaled", "voc", "volatile organic", "breathomics", "olfaction", "canine"
)

# Scoring rubric prompt
RELEVANCY_PROMPT_TEMPLATE = """You are a research relevance analyst for SpotItEarly, a company focused on early cancer detection technologies.

Analyze this publication and score its relevance (0-100) based on the following rubric:

V3 HARD CONSTRAINT LAYER (CRITICAL):
- Primary target cancers: {target_cancers}
- A paper can exceed score 60 ONLY if at least one is true:
  (A) it focuses on one of the target cancers
  (B) it is directly about detection/screening/diagnostic methodology
- If neither (A) nor (B) is true, keep score <= 60.

V3 PRIORITY HIERARCHY:
1. CANCER TYPE PRIORITY (base scoring):
   - Target cancers: strong positive base
   - Other cancers: lower base unless methodology is exceptionally detection-focused
   - Non-cancer topics: near-zero base

2. DETECTION METHOD BOOSTS (additive):
   - Breath collection/VOC/breathomics: +40 points
   - Sensor-based detection: +20 points
   - Animal model detection: +20 points
   - Biopsy/NGS/genomics: +10 points only when clearly tied to diagnostics/detection
   - Early detection/screening focus: +10 points

3. V3 NEGATIVE WEIGHTING (penalties):
   - Non-target cancers with no strong detection-method contribution: -10 to -20
   - Market/commercial-only articles (funding, TAM, competition) without method data: -20
   - Broad genomics/omics without explicit detection endpoint: -15
   - Treatment-only (no detection): -20 points
   - Review/meta-analysis (no novel method): -10 points
   - Purely computational/database: -15 points

4. AI KEYWORD DE-BIAS (IMPORTANT):
   - Do NOT boost generic AI mentions by themselves.
   - AI should increase score only when explicitly tied to detection/diagnostics outcomes.

SCORING GUIDELINES:
- Maximum score: 100
- Minimum score: 0
- Be conservative: score >=85 should be rare and reserved for true must-read items
- Score 60-79 for clearly relevant but not elite papers
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
    "cancer_type": "<breast|lung|prostate|colon|colorectal|other|none>",
    "detection_methodology": <true|false>,
    "market_only": <true|false>,
    "broad_genomics_without_detection": <true|false>,
    "ai_diagnostics_linked": <true|false>,
    "breath_based": <true|false>,
    "animal_model": <true|false>,
    "ngs_genomics": <true|false>
  }}
}}

Respond ONLY with valid JSON. Do not include markdown formatting or explanations outside the JSON object."""


def _get_target_cancer_types() -> Set[str]:
    """Get normalized target cancer set from env or defaults."""
    raw = os.environ.get("SPOTITEARLY_TARGET_CANCER_TYPES", "")
    if not raw.strip():
        return set(DEFAULT_TARGET_CANCER_TYPES)

    normalized: Set[str] = set()
    for token in raw.split(","):
        val = token.strip().lower()
        if not val:
            continue
        if val == "colorectal":
            val = "colon"
        normalized.add(val)
    return normalized or set(DEFAULT_TARGET_CANCER_TYPES)


def _normalize_cancer_type(value: Optional[str]) -> str:
    """Normalize cancer type labels for rule checks."""
    normalized = (value or "").strip().lower()
    if normalized == "colorectal":
        return "colon"
    return normalized


def _contains_any_keyword(text: str, keywords: Tuple[str, ...]) -> bool:
    """Check if any keyword appears in normalized text."""
    lower = text.lower()
    for keyword in keywords:
        if keyword in lower:
            return True
    return False


def _is_market_only_text(text: str) -> bool:
    """Heuristic for market/commercial-only content."""
    lower = text.lower()
    market_hit = _contains_any_keyword(lower, MARKET_KEYWORDS)
    detection_hit = _contains_any_keyword(lower, DETECTION_KEYWORDS)
    return market_hit and not detection_hit


def _is_broad_genomics_without_detection(text: str) -> bool:
    """Heuristic for omics/genomics papers lacking detection framing."""
    lower = text.lower()
    genomics_hit = _contains_any_keyword(lower, GENOMICS_KEYWORDS)
    detection_hit = _contains_any_keyword(lower, DETECTION_KEYWORDS)
    return genomics_hit and not detection_hit


def _is_treatment_only_context(text: str) -> bool:
    """Heuristic for treatment-oriented studies lacking detection endpoints."""
    lower = text.lower()
    treatment_hit = _contains_any_keyword(lower, TREATMENT_KEYWORDS)
    detection_hit = _contains_any_keyword(lower, DETECTION_KEYWORDS)
    return treatment_hit and not detection_hit


def _is_basic_biology_without_detection(text: str) -> bool:
    """Heuristic for mechanistic/basic biology studies without detection link."""
    lower = text.lower()
    biology_hit = _contains_any_keyword(lower, BASIC_BIOLOGY_KEYWORDS)
    detection_hit = _contains_any_keyword(lower, DETECTION_KEYWORDS)
    return biology_hit and not detection_hit


def _has_screening_impact_signal(text: str) -> bool:
    """Detect strong population-screening strategic relevance."""
    return _contains_any_keyword(text, SCREENING_IMPACT_KEYWORDS)


def _has_mced_screening_combo(text: str) -> bool:
    """Detect MCED blood-test papers tied to established screening programs."""
    lower = text.lower()
    mced_hit = ("multi-cancer early detection" in lower) or ("mced" in lower)
    blood_hit = ("blood test" in lower) or ("blood-based" in lower) or ("genomic blood test" in lower)
    complement_hit = ("complement existing screening" in lower) or ("existing screening" in lower)
    return mced_hit and blood_hit and complement_hit


def _has_breath_detection_bridge_signal(text: str) -> bool:
    """Identify breath/VOC studies that should not be overly demoted."""
    return _contains_any_keyword(text, BREATH_MODALITY_KEYWORDS)


def _has_detection_methodology_link(text: str, signals: Dict) -> bool:
    """Determine if item has explicit detection/diagnostics relevance."""
    if signals.get("detection_methodology") is True:
        return True
    if signals.get("breath_based") or signals.get("animal_model") or signals.get("ngs_genomics"):
        return True
    return _contains_any_keyword(text, DETECTION_KEYWORDS)


def _is_ai_diagnostics_linked(text: str, signals: Dict) -> bool:
    """Check whether AI appears in direct diagnostic/detection context."""
    if signals.get("ai_diagnostics_linked") is True:
        return True

    lower = text.lower()
    if not _contains_any_keyword(lower, GENERIC_AI_KEYWORDS):
        return False

    # Require AI mention near detection/diagnostic terms in same sentence window.
    windows = re.split(r"[.!?;\n]+", lower)
    for window in windows:
        if _contains_any_keyword(window, GENERIC_AI_KEYWORDS) and _contains_any_keyword(window, DETECTION_KEYWORDS):
            return True
    return False


def _normalize_score_distribution(score: int) -> int:
    """Compress top-end scores so >=85 remains rare and meaningful."""
    if score >= 85:
        return min(100, 85 + round((score - 85) * 0.35))
    if score >= 70:
        return max(0, score - 5)
    return score


def _apply_v3_business_rules(item: Dict, parsed_result: Dict) -> Dict:
    """Apply deterministic V3 constraints/penalties on top of LLM output."""
    base_score = int(parsed_result.get("relevancy_score", 0))
    reason = parsed_result.get("relevancy_reason", "").strip()
    signals = parsed_result.get("signals", {}) if isinstance(parsed_result.get("signals"), dict) else {}

    title = item.get("title", "") or ""
    abstract = item.get("raw_text") or item.get("summary") or item.get("one_liner") or ""
    source = item.get("source", "") or ""
    combined_text = f"{title}\n{source}\n{abstract}"

    target_cancers = _get_target_cancer_types()
    cancer_type = _normalize_cancer_type(signals.get("cancer_type"))
    target_cancer_match = cancer_type in target_cancers or cancer_type == "multi"
    detection_methodology_link = _has_detection_methodology_link(combined_text, signals)

    market_only = bool(signals.get("market_only")) or _is_market_only_text(combined_text)
    broad_genomics_without_detection = bool(signals.get("broad_genomics_without_detection")) or _is_broad_genomics_without_detection(combined_text)
    treatment_only = _is_treatment_only_context(combined_text)
    basic_biology_without_detection = _is_basic_biology_without_detection(combined_text)
    screening_impact = _has_screening_impact_signal(combined_text)
    mced_screening_combo = _has_mced_screening_combo(combined_text)
    breath_bridge = _has_breath_detection_bridge_signal(combined_text)
    ai_present = _contains_any_keyword(combined_text, GENERIC_AI_KEYWORDS)
    ai_diagnostics_linked = _is_ai_diagnostics_linked(combined_text, signals)

    # If paper clearly has population-screening impact, ignore broad-genomics penalty.
    if broad_genomics_without_detection and (screening_impact or mced_screening_combo):
        broad_genomics_without_detection = False
        adjustments = ["broad genomics penalty waived (screening-impact context)"]
    else:
        adjustments = []

    penalty = 0
    bonus = 0

    # Hard constraint: no target-cancer and no detection-method link => cap at 60.
    if not target_cancer_match and not detection_methodology_link and base_score > 60:
        base_score = 60
        adjustments.append("hard cap at 60 (non-target and no direct detection-method link)")

    # Negative weighting requirements.
    if cancer_type not in {"", "none", "other", "multi"} and not target_cancer_match:
        penalty += 12
        adjustments.append("non-target cancer penalty (-12)")

    if market_only:
        penalty += 20
        adjustments.append("market-only content penalty (-20)")

    if broad_genomics_without_detection:
        penalty += 15
        adjustments.append("broad genomics without detection link penalty (-15)")

    if treatment_only:
        penalty += 35
        adjustments.append("treatment-only context penalty (-35)")

    if basic_biology_without_detection:
        penalty += 20
        adjustments.append("basic biology without detection link penalty (-20)")

    # AI de-bias requirement.
    if ai_present and not ai_diagnostics_linked:
        penalty += 8
        adjustments.append("generic AI mention de-biased (-8)")
    elif ai_present and ai_diagnostics_linked:
        bonus += 5
        adjustments.append("AI tied to diagnostics bonus (+5)")

    if screening_impact and detection_methodology_link:
        bonus += 20
        adjustments.append("population-screening impact boost (+20)")

    if mced_screening_combo:
        bonus += 15
        adjustments.append("MCED + existing-screening strategic boost (+15)")

    if breath_bridge and detection_methodology_link and not treatment_only:
        bonus += 10
        adjustments.append("breath/VOC bridge boost (+10)")

    adjusted_score = max(0, min(100, base_score - penalty + bonus))

    if treatment_only and adjusted_score > 20:
        adjusted_score = 20
        adjustments.append("treatment-only cap at 20")

    normalized_score = _normalize_score_distribution(adjusted_score)

    if normalized_score != adjusted_score:
        adjustments.append(f"top-end normalization ({adjusted_score}→{normalized_score})")

    if adjustments:
        reason_suffix = "; ".join(adjustments)
        reason = f"{reason} | V3 adjustments: {reason_suffix}".strip(" |")

    enriched_signals = dict(signals)
    enriched_signals.update({
        "target_cancer_match": target_cancer_match,
        "detection_methodology": detection_methodology_link,
        "market_only": market_only,
        "broad_genomics_without_detection": broad_genomics_without_detection,
        "treatment_only": treatment_only,
        "basic_biology_without_detection": basic_biology_without_detection,
        "screening_impact": screening_impact,
        "mced_screening_combo": mced_screening_combo,
        "breath_bridge": breath_bridge,
        "ai_diagnostics_linked": ai_diagnostics_linked,
    })

    return {
        "relevancy_score": normalized_score,
        "relevancy_reason": reason,
        "signals": enriched_signals,
    }


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
            - scoring_version: "poc_v3"
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

    # Check item cache: if already scored with poc_v3 and has valid score, return cached
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
    target_cancers = sorted(_get_target_cancer_types())
    prompt = RELEVANCY_PROMPT_TEMPLATE.format(
        target_cancers=", ".join(target_cancers),
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
        adjusted = _apply_v3_business_rules(item, parsed_result)
        result = {
            "relevancy_score": adjusted["relevancy_score"],
            "relevancy_reason": adjusted["relevancy_reason"],
            "confidence": parsed_result["confidence"],
            "signals": adjusted["signals"],
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
