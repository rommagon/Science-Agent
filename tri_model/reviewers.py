"""Reviewer implementations for Claude and Gemini.

This module provides review functions that call Claude and Gemini APIs
to analyze publications and return structured reviews.
"""

import logging
import os
import time
import concurrent.futures
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
from tri_model.json_utils import extract_json_object, normalize_review_json

logger = logging.getLogger(__name__)


def _parse_review_json(response_text: str, prompt_version: str) -> Dict:
    """Parse and validate review JSON response.

    Args:
        response_text: Raw model response

    Returns:
        Parsed dict or None if invalid
    """
    data = extract_json_object(response_text)
    data = normalize_review_json(data, prompt_version)
    logger.debug("Normalized review types: %s", {k: type(v).__name__ for k, v in data.items()})

    required_fields = ["relevancy_score", "relevancy_reason", "signals", "summary", "confidence"]
    missing = sorted([field for field in required_fields if field not in data])
    if missing:
        logger.warning("Review response missing required fields: %s", missing)
        raise ValueError(f"Missing required fields: {missing}")

    type_mismatches = []
    if not isinstance(data.get("relevancy_score"), int):
        type_mismatches.append(("relevancy_score", "int", type(data.get("relevancy_score")).__name__))
    if not isinstance(data.get("relevancy_reason"), str):
        type_mismatches.append(("relevancy_reason", "str", type(data.get("relevancy_reason")).__name__))
    if not isinstance(data.get("summary"), str):
        type_mismatches.append(("summary", "str", type(data.get("summary")).__name__))
    if not isinstance(data.get("signals"), dict):
        type_mismatches.append(("signals", "dict", type(data.get("signals")).__name__))
    if "concerns" in data and not isinstance(data.get("concerns"), list):
        type_mismatches.append(("concerns", "list", type(data.get("concerns")).__name__))
    if not isinstance(data.get("confidence"), str):
        type_mismatches.append(("confidence", "str", type(data.get("confidence")).__name__))

    if type_mismatches:
        logger.warning("Review response type mismatches: %s", type_mismatches)
        raise ValueError(f"Type mismatches: {type_mismatches}")

    if not (0 <= data["relevancy_score"] <= 100):
        logger.warning("Invalid relevancy_score: %s", data.get("relevancy_score"))
        raise ValueError("relevancy_score out of range")

    if data["confidence"] not in ["low", "medium", "high"]:
        logger.warning("Invalid confidence: %s", data.get("confidence"))
        raise ValueError("confidence must be low/medium/high")

    return data


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

    prompt_version = os.getenv("TRI_MODEL_PROMPT_VERSION", CLAUDE_REVIEW_VERSION)
    prompt = get_claude_prompt(title, source, abstract, version=prompt_version)

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
    parse_errors = []

    for attempt in range(MAX_REVIEW_RETRIES):
        try:
            from anthropic import Anthropic

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

            try:
                parsed_review = _parse_review_json(response_text, prompt_version)
            except Exception as e:
                parse_error = f"{type(e).__name__}: {e}"
                parse_errors.append(parse_error)
                logger.warning(
                    "Failed to parse Claude response on attempt %d (model=%s): %s",
                    attempt + 1,
                    model_used,
                    parse_error,
                )
                logger.warning("Claude response snippet: %s", response_text[:300])
                parsed_review = None

            if parsed_review:
                logger.info("Successfully got Claude review for: %s (score=%d, model=%s)",
                           title[:80], parsed_review["relevancy_score"], model_used)
                break

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
        error_message = f"Failed to parse response after {MAX_REVIEW_RETRIES} attempts"
        if parse_errors:
            error_message = f"{error_message}: {parse_errors[-1]}"
        return {
            "success": False,
            "review": None,
            "model": successful_model or CLAUDE_MODEL,
            "version": CLAUDE_REVIEW_VERSION,
            "latency_ms": latency_ms,
            "error": error_message,
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

    prompt_version = os.getenv("TRI_MODEL_PROMPT_VERSION", GEMINI_REVIEW_VERSION)
    prompt = get_gemini_prompt(title, source, abstract, version=prompt_version)

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
    parse_errors = []

    for attempt in range(MAX_REVIEW_RETRIES):
        try:
            try:
                from importlib import metadata as importlib_metadata
            except ImportError:
                import importlib_metadata  # type: ignore

            if not hasattr(importlib_metadata, "packages_distributions"):
                try:
                    from importlib_metadata import packages_distributions as _pkg_dist  # type: ignore
                    setattr(importlib_metadata, "packages_distributions", _pkg_dist)
                except Exception:
                    setattr(importlib_metadata, "packages_distributions", lambda: {})

            # TODO: Migrate from deprecated google.generativeai to google.genai.
            try:
                import google.generativeai as genai  # type: ignore
            except Exception as import_err:
                logger.warning("Gemini import failed: %s", import_err)
                raise

            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel(GEMINI_MODEL)

            logger.info("Calling Gemini API (attempt %d/%d) for: %s", attempt + 1, MAX_REVIEW_RETRIES, title[:80])

            # Final message sanitization before API call
            sanitized_prompt = sanitize_for_llm(prompt)
            sanitized_prompt = sanitized_prompt.encode("utf-8", "replace").decode("utf-8")

            def _call_model():
                return model.generate_content(
                    sanitized_prompt,
                    generation_config={
                        "temperature": 0.3,
                        "max_output_tokens": 1024,
                    },
                    request_options={"timeout": REVIEW_TIMEOUT_SECONDS},
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_call_model)
                try:
                    response = future.result(timeout=REVIEW_TIMEOUT_SECONDS + 5)
                except concurrent.futures.TimeoutError as timeout_err:
                    raise TimeoutError("Gemini generate_content timed out") from timeout_err

            response_text = response.text

            try:
                parsed_review = _parse_review_json(response_text, prompt_version)
            except Exception as e:
                parse_error = f"{type(e).__name__}: {e}"
                parse_errors.append(parse_error)
                logger.warning(
                    "Failed to parse Gemini response on attempt %d (model=%s): %s",
                    attempt + 1,
                    GEMINI_MODEL,
                    parse_error,
                )
                logger.warning("Gemini response snippet: %s", response_text[:300])
                parsed_review = None

            if parsed_review:
                logger.info("Successfully got Gemini review for: %s (score=%d)",
                           title[:80], parsed_review["relevancy_score"])
                break

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
        error_message = f"Failed to parse response after {MAX_REVIEW_RETRIES} attempts"
        if parse_errors:
            error_message = f"{error_message}: {parse_errors[-1]}"
        return {
            "success": False,
            "review": None,
            "model": GEMINI_MODEL,
            "version": GEMINI_REVIEW_VERSION,
            "latency_ms": latency_ms,
            "error": error_message,
            "reviewed_at": datetime.now().isoformat(),
        }
