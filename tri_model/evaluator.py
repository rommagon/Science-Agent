"""GPT Evaluator for tri-model review system.

This module implements the GPT evaluator that analyzes Claude and Gemini reviews
and produces a final authoritative decision.
"""

import logging
import os
import time
from typing import Dict, Optional, Any
from datetime import datetime

from config.tri_model_config import (
    GPT_EVALUATOR_VERSION,
    GPT_EVALUATOR_MODEL,
    REVIEW_TIMEOUT_SECONDS,
    MAX_REVIEW_RETRIES,
)
from tri_model.prompts import get_gpt_evaluator_prompt
from tri_model.text_sanitize import sanitize_for_llm, sanitize_paper_for_review
from tri_model.json_utils import extract_json_object

# Import sanitize_secret for API key sanitization
from config.tri_model_config import sanitize_secret

logger = logging.getLogger(__name__)


def _score_to_rating_0_3(score: int) -> int:
    """Map 0-100 score to 0-3 bucket."""
    if score >= 75:
        return 3
    if score >= 50:
        return 2
    if score >= 25:
        return 1
    return 0


def _compute_agreement(
    claude_review: Optional[Dict[str, Any]],
    gemini_review: Optional[Dict[str, Any]],
) -> tuple:
    """Compute agreement level deterministically from reviewer scores.

    Uses the thresholds documented in the evaluator prompts:
    score gap <= 15 -> "high", <= 30 -> "moderate", > 30 -> "low".

    Args:
        claude_review: Claude's review dict (or None if unavailable)
        gemini_review: Gemini's review dict (or None if unavailable)

    Returns:
        Tuple of (agreement_level, disagreements). agreement_level is None
        when fewer than two reviewer scores are available.
    """
    claude_score = (claude_review or {}).get("relevancy_score")
    gemini_score = (gemini_review or {}).get("relevancy_score")

    if not isinstance(claude_score, int) or not isinstance(gemini_score, int):
        return None, []

    gap = abs(claude_score - gemini_score)
    if gap <= 15:
        return "high", []
    if gap <= 30:
        return "moderate", []
    return "low", [
        f"Reviewer relevancy scores diverge by {gap} points "
        f"(Claude={claude_score}, Gemini={gemini_score})"
    ]


def _merge_review_signals(claude_review: Optional[Dict[str, Any]], gemini_review: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge reviewer signals for deterministic post-processing."""
    claude_signals = (claude_review or {}).get("signals") or {}
    gemini_signals = (gemini_review or {}).get("signals") or {}

    merged: Dict[str, Any] = {}
    bool_keys = [
        "early_detection_focus",
        "screening_study",
        "risk_stratification",
        "biomarker_discovery",
        "ctdna_cfdna",
        "imaging_based",
        "prospective_cohort",
        "breath_voc",
        "urine_based",
        "sensor_based",
        "canine_detection",
        "human_subjects",
    ]
    for key in bool_keys:
        merged[key] = bool(claude_signals.get(key)) or bool(gemini_signals.get(key))

    # Backward-compatible aliases expected by mcp_server.llm_relevancy post-processor.
    merged["breath_based"] = merged["breath_voc"]
    merged["animal_model"] = merged["canine_detection"]
    merged["ngs_genomics"] = merged["biomarker_discovery"] or bool(claude_signals.get("ngs_genomics")) or bool(gemini_signals.get("ngs_genomics"))
    merged["detection_methodology"] = (
        merged["early_detection_focus"]
        or merged["screening_study"]
        or merged["breath_voc"]
        or merged["sensor_based"]
        or merged["canine_detection"]
        or merged["ctdna_cfdna"]
        or merged["imaging_based"]
    )

    # Cancer type merge with conservative priority.
    candidates = [
        str(claude_signals.get("cancer_type") or "").lower(),
        str(gemini_signals.get("cancer_type") or "").lower(),
    ]
    candidates = [c for c in candidates if c]
    if not candidates:
        merged["cancer_type"] = "none"
    elif len(set(candidates)) == 1:
        merged["cancer_type"] = candidates[0]
    else:
        priority = ["breast", "lung", "prostate", "colon", "colorectal", "multi", "other", "none"]
        chosen = next((p for p in priority if p in candidates), candidates[0])
        merged["cancer_type"] = chosen

    return merged


def _apply_v3_postprocessing(
    paper: Dict[str, Any],
    parsed_evaluation: Dict[str, Any],
    claude_review: Optional[Dict[str, Any]],
    gemini_review: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Apply mcp_server V3.2 deterministic scoring rules to tri-model final score."""
    prompt_version = os.getenv("TRI_MODEL_PROMPT_VERSION", GPT_EVALUATOR_VERSION)
    if prompt_version != "v3":
        return parsed_evaluation

    try:
        from mcp_server.llm_relevancy import _apply_v3_business_rules  # Reuse canonical V3.2 rule engine.
    except Exception as e:
        logger.warning("V3.2 postprocessing unavailable, keeping raw GPT score: %s", e)
        return parsed_evaluation

    merged_signals = _merge_review_signals(claude_review, gemini_review)
    item = {
        "title": paper.get("title", ""),
        "source": paper.get("source", ""),
        "raw_text": paper.get("raw_text") or paper.get("summary") or "",
        "summary": paper.get("summary", ""),
        "one_liner": "",
    }
    parsed_result = {
        "relevancy_score": int(parsed_evaluation.get("final_relevancy_score", 0)),
        "relevancy_reason": parsed_evaluation.get("final_relevancy_reason", ""),
        "signals": merged_signals,
    }

    adjusted = _apply_v3_business_rules(item, parsed_result)
    parsed_evaluation["final_relevancy_score"] = int(adjusted["relevancy_score"])
    parsed_evaluation["final_relevancy_rating_0_3"] = _score_to_rating_0_3(parsed_evaluation["final_relevancy_score"])
    parsed_evaluation["final_relevancy_reason"] = adjusted["relevancy_reason"]
    parsed_evaluation["final_signals"] = adjusted.get("signals", {})
    return parsed_evaluation


def _parse_evaluator_json(response_text: str) -> Dict:
    """Parse and validate evaluator JSON response.

    Args:
        response_text: Raw GPT response

    Returns:
        Parsed dict or None if invalid
    """
    data = extract_json_object(response_text)

    # Accept minimal evaluator schema and normalize to canonical fields
    required_fields = [
        "final_relevancy_rating_0_3",
        "final_relevancy_score",
        "final_relevancy_reason",
    ]

    missing = sorted([field for field in required_fields if field not in data])
    if missing:
        logger.warning("Evaluator response missing required fields: %s", missing)
        raise ValueError(f"Missing required fields: {missing}")

    type_mismatches = []
    if not isinstance(data.get("final_relevancy_score"), int):
        type_mismatches.append(("final_relevancy_score", "int", type(data.get("final_relevancy_score")).__name__))
    if not isinstance(data.get("final_relevancy_rating_0_3"), int):
        type_mismatches.append(("final_relevancy_rating_0_3", "int", type(data.get("final_relevancy_rating_0_3")).__name__))
    if not isinstance(data.get("final_relevancy_reason"), str):
        type_mismatches.append(("final_relevancy_reason", "str", type(data.get("final_relevancy_reason")).__name__))

    if type_mismatches:
        logger.warning("Evaluator response type mismatches: %s", type_mismatches)
        raise ValueError(f"Type mismatches: {type_mismatches}")

    if not (0 <= data["final_relevancy_score"] <= 100):
        logger.warning("Invalid final_relevancy_score: %s", data.get("final_relevancy_score"))
        raise ValueError("final_relevancy_score out of range")

    if not (0 <= data["final_relevancy_rating_0_3"] <= 3):
        logger.warning("Invalid final_relevancy_rating_0_3: %s", data.get("final_relevancy_rating_0_3"))
        raise ValueError("final_relevancy_rating_0_3 out of range")

    # Default confidence if missing
    if "confidence" not in data or data.get("confidence") is None:
        data["confidence"] = 60
    try:
        data["confidence"] = int(max(0, min(100, int(data["confidence"]))))
    except (TypeError, ValueError):
        data["confidence"] = 60

    # Normalize to canonical evaluator fields used downstream
    if "final_signals" not in data:
        data["final_signals"] = {}
    if "final_summary" not in data:
        data["final_summary"] = data.get("final_relevancy_reason", "")
    # agreement_level/disagreements are computed deterministically from the
    # reviewer scores in gpt_evaluate() (the GPT prompts forbid extra keys).
    if "agreement_level" not in data:
        data["agreement_level"] = None
    if "disagreements" not in data:
        data["disagreements"] = []
    if "evaluator_rationale" not in data:
        data["evaluator_rationale"] = data.get("final_relevancy_reason", "")

    return data


def gpt_evaluate(
    paper: Dict,
    claude_result: Dict,
    gemini_result: Dict,
) -> Dict:
    """Evaluate Claude and Gemini reviews using GPT.

    Args:
        paper: Publication dict with title, source, raw_text/summary
        claude_result: Result from claude_review() (may have success=False)
        gemini_result: Result from gemini_review() (may have success=False)

    Returns:
        Evaluation result dict:
        {
            "success": bool,
            "evaluation": dict or None,
            "model": "<GPT_EVALUATOR_MODEL, default gpt-4o-mini>",
            "version": "v1",
            "latency_ms": int,
            "error": str or None,
            "evaluated_at": ISO timestamp,
            "inputs_used": {
                "claude_available": bool,
                "gemini_available": bool
            }
        }
    """
    # Get OpenAI API key and sanitize it to remove unicode/control characters
    api_key = sanitize_secret(os.getenv("SPOTITEARLY_LLM_API_KEY") or os.getenv("OPENAI_API_KEY"))
    if not api_key:
        return {
            "success": False,
            "evaluation": None,
            "model": GPT_EVALUATOR_MODEL,
            "version": GPT_EVALUATOR_VERSION,
            "latency_ms": 0,
            "error": "OpenAI API key not configured",
            "evaluated_at": datetime.now().isoformat(),
            "inputs_used": {
                "claude_available": False,
                "gemini_available": False,
            }
        }

    # Sanitize paper at entry point to remove unicode control characters
    paper = sanitize_paper_for_review(paper)

    title = paper.get("title", "")
    source = paper.get("source", "")
    abstract = paper.get("raw_text") or paper.get("summary") or ""

    if not title:
        return {
            "success": False,
            "evaluation": None,
            "model": GPT_EVALUATOR_MODEL,
            "version": GPT_EVALUATOR_VERSION,
            "latency_ms": 0,
            "error": "Missing title",
            "evaluated_at": datetime.now().isoformat(),
            "inputs_used": {
                "claude_available": claude_result.get("success", False),
                "gemini_available": gemini_result.get("success", False),
            }
        }

    # Extract reviews (may be None)
    claude_review = claude_result.get("review") if claude_result.get("success") else None
    gemini_review = gemini_result.get("review") if gemini_result.get("success") else None

    # Check if we have at least one review
    if not claude_review and not gemini_review:
        return {
            "success": False,
            "evaluation": None,
            "model": GPT_EVALUATOR_MODEL,
            "version": GPT_EVALUATOR_VERSION,
            "latency_ms": 0,
            "error": "No reviews available to evaluate (both Claude and Gemini failed)",
            "evaluated_at": datetime.now().isoformat(),
            "inputs_used": {
                "claude_available": False,
                "gemini_available": False,
            }
        }

    prompt_version = os.getenv("TRI_MODEL_PROMPT_VERSION", GPT_EVALUATOR_VERSION)
    prompt = get_gpt_evaluator_prompt(
        title,
        source,
        abstract,
        claude_review,
        gemini_review,
        version=prompt_version,
    )

    # Final sanitization of prompt string before API call (last mile defense)
    prompt = sanitize_for_llm(prompt)

    # Extra hardening: UTF-8 encode/decode to prevent implicit ascii encoding
    # This ensures that even if sanitization missed something, we won't crash
    try:
        prompt = prompt.encode("utf-8", "replace").decode("utf-8")
    except Exception as encode_err:
        logger.warning("UTF-8 encoding hardening failed: %s", encode_err)

    # Call GPT API with retry logic
    start_time = time.time()
    parsed_evaluation = None
    parse_errors = []

    for attempt in range(MAX_REVIEW_RETRIES):
        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key)

            logger.info("Calling GPT evaluator (attempt %d/%d) for: %s",
                       attempt + 1, MAX_REVIEW_RETRIES, title[:80])

            # Final message sanitization before API call
            system_msg = "You are a meta-evaluator. Respond only with valid JSON."
            system_msg = sanitize_for_llm(system_msg).encode("utf-8", "replace").decode("utf-8")

            user_msg = sanitize_for_llm(prompt).encode("utf-8", "replace").decode("utf-8")

            response = client.chat.completions.create(
                model=GPT_EVALUATOR_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": system_msg
                    },
                    {
                        "role": "user",
                        "content": user_msg
                    }
                ],
                temperature=0,
                response_format={"type": "json_object"},
                max_completion_tokens=1024,
                timeout=REVIEW_TIMEOUT_SECONDS,
            )

            response_text = response.choices[0].message.content

            try:
                parsed_evaluation = _parse_evaluator_json(response_text)
            except Exception as e:
                parse_error = f"{type(e).__name__}: {e}"
                parse_errors.append(parse_error)
                logger.warning(
                    "Failed to parse GPT evaluation on attempt %d: %s",
                    attempt + 1,
                    parse_error,
                )
                logger.warning("GPT response snippet: %s", response_text[:300])
                parsed_evaluation = None

            if parsed_evaluation:
                logger.info("Successfully got GPT evaluation for: %s (final_score=%d)",
                           title[:80], parsed_evaluation["final_relevancy_score"])
                break

        except Exception as e:
            # Diagnostic: check for unicode issues in the prompt
            try:
                u2028_count = user_msg.count('\u2028') if 'user_msg' in locals() else prompt.count('\u2028')
                u2029_count = user_msg.count('\u2029') if 'user_msg' in locals() else prompt.count('\u2029')
                if u2028_count > 0 or u2029_count > 0:
                    logger.error("Unicode separators detected in prompt: U+2028=%d, U+2029=%d", u2028_count, u2029_count)
            except:
                pass  # Don't let diagnostic fail the error handling

            logger.warning("GPT API call failed on attempt %d: %s", attempt + 1, str(e))
            if attempt == MAX_REVIEW_RETRIES - 1:
                # Last attempt failed
                latency_ms = int((time.time() - start_time) * 1000)
                return {
                    "success": False,
                    "evaluation": None,
                    "model": GPT_EVALUATOR_MODEL,
                    "version": GPT_EVALUATOR_VERSION,
                    "latency_ms": latency_ms,
                    "error": f"API error after {MAX_REVIEW_RETRIES} attempts: {str(e)}",
                    "evaluated_at": datetime.now().isoformat(),
                    "inputs_used": {
                        "claude_available": claude_review is not None,
                        "gemini_available": gemini_review is not None,
                    }
                }

    latency_ms = int((time.time() - start_time) * 1000)

    if parsed_evaluation:
        parsed_evaluation = _apply_v3_postprocessing(
            paper=paper,
            parsed_evaluation=parsed_evaluation,
            claude_review=claude_review,
            gemini_review=gemini_review,
        )
        # Compute agreement deterministically from reviewer scores (the GPT
        # prompts forbid extra keys, so the model cannot report this itself).
        agreement_level, disagreements = _compute_agreement(claude_review, gemini_review)
        parsed_evaluation["agreement_level"] = agreement_level
        parsed_evaluation["disagreements"] = disagreements
        return {
            "success": True,
            "evaluation": parsed_evaluation,
            "model": GPT_EVALUATOR_MODEL,
            "version": GPT_EVALUATOR_VERSION,
            "latency_ms": latency_ms,
            "error": None,
            "evaluated_at": datetime.now().isoformat(),
            "inputs_used": {
                "claude_available": claude_review is not None,
                "gemini_available": gemini_review is not None,
            }
        }
    else:
        error_message = f"Failed to parse response after {MAX_REVIEW_RETRIES} attempts"
        if parse_errors:
            error_message = f"{error_message}: {parse_errors[-1]}"
        return {
            "success": False,
            "evaluation": None,
            "model": GPT_EVALUATOR_MODEL,
            "version": GPT_EVALUATOR_VERSION,
            "latency_ms": latency_ms,
            "error": error_message,
            "evaluated_at": datetime.now().isoformat(),
            "inputs_used": {
                "claude_available": claude_review is not None,
                "gemini_available": gemini_review is not None,
            }
        }


_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def reviewer_fallback_evaluate(
    paper: Dict,
    claude_result: Optional[Dict],
    gemini_result: Optional[Dict],
    error: Optional[str] = None,
) -> Dict:
    """Build a deterministic evaluation from reviewer outputs when GPT fails.

    Used so a GPT evaluator outage does not drop papers that at least one
    reviewer scored successfully. The aggregate is fully deterministic:
    - final_relevancy_score: mean of available reviewer relevancy scores
    - confidence: minimum of available reviewer confidences (or "low")
    - final_relevancy_rating_0_3: same thresholds as the evaluator/V3 rules

    Args:
        paper: Publication dict with title, source, raw_text/summary
        claude_result: Result from claude_review() (may be None or success=False)
        gemini_result: Result from gemini_review() (may be None or success=False)
        error: Optional error message from the failed GPT evaluation

    Returns:
        Result dict with the same shape as gpt_evaluate(), with
        model="reviewer-fallback" and evaluator_fallback=True for provenance.
    """
    claude_review = (claude_result or {}).get("review") if (claude_result or {}).get("success") else None
    gemini_review = (gemini_result or {}).get("review") if (gemini_result or {}).get("success") else None

    inputs_used = {
        "claude_available": claude_review is not None,
        "gemini_available": gemini_review is not None,
    }

    available_reviews = [r for r in (claude_review, gemini_review) if r]
    if not available_reviews:
        return {
            "success": False,
            "evaluation": None,
            "model": "reviewer-fallback",
            "version": GPT_EVALUATOR_VERSION,
            "latency_ms": 0,
            "error": "No reviews available for fallback aggregation (both Claude and Gemini failed)",
            "evaluator_fallback": True,
            "evaluated_at": datetime.now().isoformat(),
            "inputs_used": inputs_used,
        }

    scores = [int(r.get("relevancy_score", 0)) for r in available_reviews]
    final_score = int(round(sum(scores) / len(scores)))

    confidences = [r.get("confidence") for r in available_reviews if r.get("confidence") in _CONFIDENCE_RANK]
    confidence = min(confidences, key=lambda c: _CONFIDENCE_RANK[c]) if confidences else "low"

    reviewer_reasons = []
    if claude_review:
        reviewer_reasons.append(f"Claude: {claude_review.get('relevancy_reason', '')}")
    if gemini_review:
        reviewer_reasons.append(f"Gemini: {gemini_review.get('relevancy_reason', '')}")

    note = "GPT evaluator unavailable; score is the deterministic mean of reviewer scores."
    final_reason = " ".join([note] + reviewer_reasons)

    summaries = [r.get("summary") for r in available_reviews if r.get("summary")]
    final_summary = summaries[0] if summaries else final_reason

    agreement_level, disagreements = _compute_agreement(claude_review, gemini_review)

    rationale = f"{note} Evaluator error: {error}" if error else note

    evaluation = {
        "final_relevancy_rating_0_3": _score_to_rating_0_3(final_score),
        "final_relevancy_score": final_score,
        "final_relevancy_reason": final_reason,
        "final_signals": _merge_review_signals(claude_review, gemini_review),
        "final_summary": final_summary,
        "agreement_level": agreement_level,
        "disagreements": disagreements,
        "evaluator_rationale": rationale,
        "confidence": confidence,
        "evaluator_fallback": True,
    }

    return {
        "success": True,
        "evaluation": evaluation,
        "model": "reviewer-fallback",
        "version": GPT_EVALUATOR_VERSION,
        "latency_ms": 0,
        "error": None,
        "evaluator_fallback": True,
        "evaluated_at": datetime.now().isoformat(),
        "inputs_used": inputs_used,
    }
