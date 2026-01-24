"""GPT Evaluator for tri-model review system.

This module implements the GPT evaluator that analyzes Claude and Gemini reviews
and produces a final authoritative decision.
"""

import json
import logging
import os
import time
from typing import Dict, Optional
from datetime import datetime

from config.tri_model_config import GPT_EVALUATOR_VERSION, REVIEW_TIMEOUT_SECONDS, MAX_REVIEW_RETRIES
from tri_model.prompts import get_gpt_evaluator_prompt
from tri_model.text_sanitize import sanitize_for_llm, sanitize_paper_for_review

logger = logging.getLogger(__name__)


def _parse_evaluator_json(response_text: str) -> Optional[Dict]:
    """Parse and validate evaluator JSON response.

    Args:
        response_text: Raw GPT response

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
            lines = lines[1:]  # Remove first line
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        data = json.loads(text)

        # Validate required fields
        required_fields = [
            "final_relevancy_score",
            "final_relevancy_reason",
            "final_signals",
            "final_summary",
            "agreement_level",
            "disagreements",
            "evaluator_rationale",
            "confidence"
        ]

        if not all(field in data for field in required_fields):
            logger.warning("Evaluator response missing required fields: %s", data.keys())
            return None

        # Validate score range
        if not isinstance(data["final_relevancy_score"], int) or not (0 <= data["final_relevancy_score"] <= 100):
            logger.warning("Invalid final_relevancy_score: %s", data.get("final_relevancy_score"))
            return None

        # Validate agreement_level
        if data["agreement_level"] not in ["high", "moderate", "low"]:
            logger.warning("Invalid agreement_level: %s", data.get("agreement_level"))
            return None

        # Validate confidence
        if data["confidence"] not in ["low", "medium", "high"]:
            logger.warning("Invalid confidence: %s", data.get("confidence"))
            return None

        return data

    except json.JSONDecodeError as e:
        logger.warning("Failed to parse evaluator JSON: %s", e)
        return None
    except Exception as e:
        logger.warning("Unexpected error parsing evaluator response: %s", e)
        return None


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
            "model": "gpt-4o-mini",
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
    # Get OpenAI API key
    api_key = os.getenv("SPOTITEARLY_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {
            "success": False,
            "evaluation": None,
            "model": "gpt-4o-mini",
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
            "model": "gpt-4o-mini",
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
            "model": "gpt-4o-mini",
            "version": GPT_EVALUATOR_VERSION,
            "latency_ms": 0,
            "error": "No reviews available to evaluate (both Claude and Gemini failed)",
            "evaluated_at": datetime.now().isoformat(),
            "inputs_used": {
                "claude_available": False,
                "gemini_available": False,
            }
        }

    prompt = get_gpt_evaluator_prompt(title, source, abstract, claude_review, gemini_review)

    # Final sanitization of prompt string before API call (last mile defense)
    prompt = sanitize_for_llm(prompt)

    # Call GPT API with retry logic
    start_time = time.time()
    parsed_evaluation = None

    for attempt in range(MAX_REVIEW_RETRIES):
        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key)

            logger.info("Calling GPT evaluator (attempt %d/%d) for: %s",
                       attempt + 1, MAX_REVIEW_RETRIES, title[:80])

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a meta-evaluator. Respond only with valid JSON."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.3,
                max_completion_tokens=1024,
                timeout=REVIEW_TIMEOUT_SECONDS,
            )

            response_text = response.choices[0].message.content

            parsed_evaluation = _parse_evaluator_json(response_text)
            if parsed_evaluation:
                logger.info("Successfully got GPT evaluation for: %s (final_score=%d)",
                           title[:80], parsed_evaluation["final_relevancy_score"])
                break
            else:
                logger.warning("Failed to parse GPT evaluation on attempt %d: %s",
                              attempt + 1, response_text[:200])

        except Exception as e:
            logger.warning("GPT API call failed on attempt %d: %s", attempt + 1, str(e))
            if attempt == MAX_REVIEW_RETRIES - 1:
                # Last attempt failed
                latency_ms = int((time.time() - start_time) * 1000)
                return {
                    "success": False,
                    "evaluation": None,
                    "model": "gpt-4o-mini",
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
        return {
            "success": True,
            "evaluation": parsed_evaluation,
            "model": "gpt-4o-mini",
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
        return {
            "success": False,
            "evaluation": None,
            "model": "gpt-4o-mini",
            "version": GPT_EVALUATOR_VERSION,
            "latency_ms": latency_ms,
            "error": f"Failed to parse response after {MAX_REVIEW_RETRIES} attempts",
            "evaluated_at": datetime.now().isoformat(),
            "inputs_used": {
                "claude_available": claude_review is not None,
                "gemini_available": gemini_review is not None,
            }
        }
