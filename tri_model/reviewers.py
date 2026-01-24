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
from tri_model.text_sanitize import sanitize_for_llm, sanitize_paper_for_review

import hashlib

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

    # Sanitize paper at entry point to remove unicode control characters
    paper = sanitize_paper_for_review(paper)

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

    prompt = get_claude_prompt(title, source, abstract)

    # Final sanitization of prompt string before API call (last mile defense)
    prompt = sanitize_for_llm(prompt)

    # Extra hardening: UTF-8 encode/decode to prevent implicit ascii encoding
    # This ensures that even if sanitization missed something, we won't crash
    try:
        prompt = prompt.encode("utf-8", "replace").decode("utf-8")
    except Exception as encode_err:
        logger.warning("UTF-8 encoding hardening failed: %s", encode_err)

    # Call Claude API with retry logic and model fallback
    start_time = time.time()
    parsed_review = None
    successful_model = None

    for attempt in range(MAX_REVIEW_RETRIES):
        try:
            from anthropic import Anthropic

            # Safe debug logging for API key (only on first attempt)
            if attempt == 0:
                key_hash = hashlib.sha256(CLAUDE_API_KEY.encode('utf-8')).hexdigest()[:12]
                logger.debug("Initializing Claude client: key_length=%d, key_hash=%s",
                           len(CLAUDE_API_KEY), key_hash)

            client = Anthropic(api_key=CLAUDE_API_KEY)

            logger.info("Calling Claude API (attempt %d/%d) for: %s", attempt + 1, MAX_REVIEW_RETRIES, title[:80])

            # Final message assembly with per-message sanitization
            # Apply sanitization to the actual message content that will be sent
            sanitized_prompt = sanitize_for_llm(prompt)
            sanitized_prompt = sanitized_prompt.encode("utf-8", "replace").decode("utf-8")

            # Model fallback: Try preferred model, then fallbacks if 404 not_found_error
            models_to_try = [CLAUDE_MODEL, "claude-3-haiku-20240307", "claude-3-5-sonnet-20241022"]
            model_used = None
            last_error = None

            for model_name in models_to_try:
                try:
                    logger.info("Trying Claude model: %s", model_name)
                    response = client.messages.create(
                        model=model_name,
                        max_tokens=1024,
                        temperature=0.3,
                        messages=[
                            {
                                "role": "user",
                                "content": sanitized_prompt
                            }
                        ],
                        timeout=REVIEW_TIMEOUT_SECONDS,
                    )

                    # Success - record which model worked
                    model_used = model_name
                    successful_model = model_name
                    logger.info("Successfully called Claude with model: %s", model_name)
                    break

                except Exception as model_err:
                    last_error = model_err
                    # Check if this is a 404 model not found error
                    error_str = str(model_err).lower()
                    is_404_model_error = (
                        hasattr(model_err, 'status_code') and model_err.status_code == 404
                    ) or (
                        'not_found_error' in error_str and 'model' in error_str
                    ) or (
                        '404' in error_str and 'model' in error_str
                    )

                    if is_404_model_error:
                        logger.warning("Model %s not found (404), trying fallback", model_name)
                        continue  # Try next fallback model
                    else:
                        # Non-404 error, don't try fallbacks
                        raise model_err

            # If no model worked, raise the last error
            if not model_used:
                raise last_error

            response_text = response.content[0].text

            parsed_review = _parse_review_json(response_text)
            if parsed_review:
                logger.info("Successfully got Claude review for: %s (score=%d, model=%s)",
                           title[:80], parsed_review["relevancy_score"], model_used)
                break
            else:
                logger.warning("Failed to parse Claude response on attempt %d: %s",
                              attempt + 1, response_text[:200])

        except Exception as e:
            # Diagnostic: check for unicode issues in the prompt
            try:
                u2028_count = sanitized_prompt.count('\u2028') if 'sanitized_prompt' in locals() else prompt.count('\u2028')
                u2029_count = sanitized_prompt.count('\u2029') if 'sanitized_prompt' in locals() else prompt.count('\u2029')
                if u2028_count > 0 or u2029_count > 0:
                    logger.error("Unicode separators detected in prompt: U+2028=%d, U+2029=%d", u2028_count, u2029_count)
            except:
                pass  # Don't let diagnostic fail the error handling

            logger.warning("Claude API call failed on attempt %d: %s", attempt + 1, str(e))
            if attempt == MAX_REVIEW_RETRIES - 1:
                # Last attempt failed
                latency_ms = int((time.time() - start_time) * 1000)
                return {
                    "success": False,
                    "review": None,
                    "model": successful_model or CLAUDE_MODEL,
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
            "model": successful_model or CLAUDE_MODEL,
            "version": CLAUDE_REVIEW_VERSION,
            "latency_ms": latency_ms,
            "error": None,
            "reviewed_at": datetime.now().isoformat(),
        }
    else:
        return {
            "success": False,
            "review": None,
            "model": successful_model or CLAUDE_MODEL,
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

    # Sanitize paper at entry point to remove unicode control characters
    paper = sanitize_paper_for_review(paper)

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

    prompt = get_gemini_prompt(title, source, abstract)

    # Final sanitization of prompt string before API call (last mile defense)
    prompt = sanitize_for_llm(prompt)

    # Extra hardening: UTF-8 encode/decode to prevent implicit ascii encoding
    # This ensures that even if sanitization missed something, we won't crash
    try:
        prompt = prompt.encode("utf-8", "replace").decode("utf-8")
    except Exception as encode_err:
        logger.warning("UTF-8 encoding hardening failed: %s", encode_err)

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

            # Final message sanitization before API call
            sanitized_prompt = sanitize_for_llm(prompt)
            sanitized_prompt = sanitized_prompt.encode("utf-8", "replace").decode("utf-8")

            response = model.generate_content(
                sanitized_prompt,
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
            # Diagnostic: check for unicode issues in the prompt
            try:
                u2028_count = sanitized_prompt.count('\u2028') if 'sanitized_prompt' in locals() else prompt.count('\u2028')
                u2029_count = sanitized_prompt.count('\u2029') if 'sanitized_prompt' in locals() else prompt.count('\u2029')
                if u2028_count > 0 or u2029_count > 0:
                    logger.error("Unicode separators detected in prompt: U+2028=%d, U+2029=%d", u2028_count, u2029_count)
            except:
                pass  # Don't let diagnostic fail the error handling

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
