"""Reviewer implementations for Claude and Gemini.

This module provides review functions that call Claude and Gemini APIs
to analyze publications and return structured reviews.
"""

import json
import logging
import time
from typing import Dict, Optional
from datetime import datetime

from config.tri_model_config import (
    CLAUDE_API_KEY,
    CLAUDE_MODEL,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    CLAUDE_REVIEW_VERSION,
    GEMINI_REVIEW_VERSION,
    REVIEW_TIMEOUT_SECONDS,
    MAX_REVIEW_RETRIES,
)
from tri_model.prompts import get_claude_prompt, get_gemini_prompt
from tri_model.text_sanitize import sanitize_for_llm

logger = logging.getLogger(__name__)


def _parse_review_json(response_text: str) -> Optional[Dict]:
    """Parse and validate review JSON response.

    Args:
        response_text: Raw model response

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
        required_fields = ["relevancy_score", "relevancy_reason", "signals", "summary", "concerns", "confidence"]
        if not all(field in data for field in required_fields):
            logger.warning("Review response missing required fields: %s", data.keys())
            return None

        # Validate score range
        if not isinstance(data["relevancy_score"], int) or not (0 <= data["relevancy_score"] <= 100):
            logger.warning("Invalid relevancy_score: %s", data.get("relevancy_score"))
            return None

        # Validate confidence
        if data["confidence"] not in ["low", "medium", "high"]:
            logger.warning("Invalid confidence: %s", data.get("confidence"))
            return None

        # Validate signals structure
        if not isinstance(data["signals"], dict):
            logger.warning("Signals is not a dict: %s", type(data["signals"]))
            return None

        return data

    except json.JSONDecodeError as e:
        logger.warning("Failed to parse review JSON: %s", e)
        return None
    except Exception as e:
        logger.warning("Unexpected error parsing review: %s", e)
        return None


def claude_review(paper: Dict) -> Dict:
    """Review a paper using Claude.

    Args:
        paper: Publication dict with title, source, raw_text/summary

    Returns:
        Review result dict:
        {
            "success": bool,
            "review": dict or None,
            "model": "claude-sonnet-4-5-20250929",
            "version": "v1",
            "latency_ms": int,
            "error": str or None,
            "reviewed_at": ISO timestamp
        }
    """
    if not CLAUDE_API_KEY:
        return {
            "success": False,
            "review": None,
            "model": CLAUDE_MODEL,
            "version": CLAUDE_REVIEW_VERSION,
            "latency_ms": 0,
            "error": "CLAUDE_API_KEY not configured",
            "reviewed_at": datetime.now().isoformat(),
        }

    title = paper.get("title", "")
    source = paper.get("source", "")
    abstract = paper.get("raw_text") or paper.get("summary") or ""

    if not title:
        return {
            "success": False,
            "review": None,
            "model": CLAUDE_MODEL,
            "version": CLAUDE_REVIEW_VERSION,
            "latency_ms": 0,
            "error": "Missing title",
            "reviewed_at": datetime.now().isoformat(),
        }

    # Sanitize text to avoid unicode encoding issues (U+2028, U+2029, etc.)
    title = sanitize_for_llm(title)
    source = sanitize_for_llm(source)
    abstract = sanitize_for_llm(abstract)

    prompt = get_claude_prompt(title, source, abstract)

    # Call Claude API with retry logic
    start_time = time.time()
    parsed_review = None

    for attempt in range(MAX_REVIEW_RETRIES):
        try:
            from anthropic import Anthropic

            client = Anthropic(api_key=CLAUDE_API_KEY)

            logger.info("Calling Claude API (attempt %d/%d) for: %s", attempt + 1, MAX_REVIEW_RETRIES, title[:80])

            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1024,
                temperature=0.3,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                timeout=REVIEW_TIMEOUT_SECONDS,
            )

            response_text = response.content[0].text

            parsed_review = _parse_review_json(response_text)
            if parsed_review:
                logger.info("Successfully got Claude review for: %s (score=%d)",
                           title[:80], parsed_review["relevancy_score"])
                break
            else:
                logger.warning("Failed to parse Claude response on attempt %d: %s",
                              attempt + 1, response_text[:200])

        except Exception as e:
            logger.warning("Claude API call failed on attempt %d: %s", attempt + 1, str(e))
            if attempt == MAX_REVIEW_RETRIES - 1:
                # Last attempt failed
                latency_ms = int((time.time() - start_time) * 1000)
                return {
                    "success": False,
                    "review": None,
                    "model": CLAUDE_MODEL,
                    "version": CLAUDE_REVIEW_VERSION,
                    "latency_ms": latency_ms,
                    "error": f"API error after {MAX_REVIEW_RETRIES} attempts: {str(e)}",
                    "reviewed_at": datetime.now().isoformat(),
                }

    latency_ms = int((time.time() - start_time) * 1000)

    if parsed_review:
        return {
            "success": True,
            "review": parsed_review,
            "model": CLAUDE_MODEL,
            "version": CLAUDE_REVIEW_VERSION,
            "latency_ms": latency_ms,
            "error": None,
            "reviewed_at": datetime.now().isoformat(),
        }
    else:
        return {
            "success": False,
            "review": None,
            "model": CLAUDE_MODEL,
            "version": CLAUDE_REVIEW_VERSION,
            "latency_ms": latency_ms,
            "error": f"Failed to parse response after {MAX_REVIEW_RETRIES} attempts",
            "reviewed_at": datetime.now().isoformat(),
        }


def gemini_review(paper: Dict) -> Dict:
    """Review a paper using Gemini.

    Args:
        paper: Publication dict with title, source, raw_text/summary

    Returns:
        Review result dict (same structure as claude_review)
    """
    if not GEMINI_API_KEY:
        return {
            "success": False,
            "review": None,
            "model": GEMINI_MODEL,
            "version": GEMINI_REVIEW_VERSION,
            "latency_ms": 0,
            "error": "GEMINI_API_KEY not configured",
            "reviewed_at": datetime.now().isoformat(),
        }

    title = paper.get("title", "")
    source = paper.get("source", "")
    abstract = paper.get("raw_text") or paper.get("summary") or ""

    if not title:
        return {
            "success": False,
            "review": None,
            "model": GEMINI_MODEL,
            "version": GEMINI_REVIEW_VERSION,
            "latency_ms": 0,
            "error": "Missing title",
            "reviewed_at": datetime.now().isoformat(),
        }

    # Sanitize text to avoid unicode encoding issues (U+2028, U+2029, etc.)
    title = sanitize_for_llm(title)
    source = sanitize_for_llm(source)
    abstract = sanitize_for_llm(abstract)

    prompt = get_gemini_prompt(title, source, abstract)

    # Call Gemini API with retry logic
    start_time = time.time()
    parsed_review = None

    for attempt in range(MAX_REVIEW_RETRIES):
        try:
            # TODO: Migrate from deprecated google.generativeai to google.genai
            # The google-generativeai package is deprecated. Future versions should use:
            # from google import genai
            # See: https://ai.google.dev/gemini-api/docs/migrate-to-v1-5
            import google.generativeai as genai

            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel(GEMINI_MODEL)

            logger.info("Calling Gemini API (attempt %d/%d) for: %s", attempt + 1, MAX_REVIEW_RETRIES, title[:80])

            response = model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.3,
                    "max_output_tokens": 1024,
                },
                request_options={"timeout": REVIEW_TIMEOUT_SECONDS},
            )

            response_text = response.text

            parsed_review = _parse_review_json(response_text)
            if parsed_review:
                logger.info("Successfully got Gemini review for: %s (score=%d)",
                           title[:80], parsed_review["relevancy_score"])
                break
            else:
                logger.warning("Failed to parse Gemini response on attempt %d: %s",
                              attempt + 1, response_text[:200])

        except Exception as e:
            logger.warning("Gemini API call failed on attempt %d: %s", attempt + 1, str(e))
            if attempt == MAX_REVIEW_RETRIES - 1:
                # Last attempt failed
                latency_ms = int((time.time() - start_time) * 1000)
                return {
                    "success": False,
                    "review": None,
                    "model": GEMINI_MODEL,
                    "version": GEMINI_REVIEW_VERSION,
                    "latency_ms": latency_ms,
                    "error": f"API error after {MAX_REVIEW_RETRIES} attempts: {str(e)}",
                    "reviewed_at": datetime.now().isoformat(),
                }

    latency_ms = int((time.time() - start_time) * 1000)

    if parsed_review:
        return {
            "success": True,
            "review": parsed_review,
            "model": GEMINI_MODEL,
            "version": GEMINI_REVIEW_VERSION,
            "latency_ms": latency_ms,
            "error": None,
            "reviewed_at": datetime.now().isoformat(),
        }
    else:
        return {
            "success": False,
            "review": None,
            "model": GEMINI_MODEL,
            "version": GEMINI_REVIEW_VERSION,
            "latency_ms": latency_ms,
            "error": f"Failed to parse response after {MAX_REVIEW_RETRIES} attempts",
            "reviewed_at": datetime.now().isoformat(),
        }
