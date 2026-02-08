"""Utilities for extracting JSON objects from model responses."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _sanitize_trailing_commas(text: str) -> str:
    # Remove trailing commas before } or ]
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*]", "]", text)
    return text


def extract_json_object(text: str) -> Dict[str, Any]:
    """Extract a JSON object from free-form model output.

    Handles fenced code blocks, extra prose, and trailing commas.
    Raises ValueError if no JSON object can be found or parsed.
    """
    if not text:
        raise ValueError("Empty response; no JSON to parse")

    cleaned = _strip_code_fences(text)

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in response")

    candidate = cleaned[start : end + 1].strip()
    candidate = _sanitize_trailing_commas(candidate)

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        snippet = candidate[:300]
        raise ValueError(f"JSON decode failed: {e}; candidate snippet: {snippet}") from e


def _confidence_from_uncertainty(uncertainty: Any) -> str:
    if isinstance(uncertainty, (int, float)):
        try:
            conf = 1.0 - float(uncertainty)
        except (TypeError, ValueError):
            conf = 0.5
        if conf < 0.4:
            return "low"
        if conf < 0.7:
            return "medium"
        return "high"
    if isinstance(uncertainty, str):
        lowered = uncertainty.strip().lower()
        if "low" in lowered:
            return "high"
        if "high" in lowered:
            return "low"
        if "medium" in lowered or "moderate" in lowered:
            return "medium"
    return "medium"


def normalize_review_json(raw: Dict[str, Any], prompt_version: str) -> Dict[str, Any]:
    """Normalize reviewer output to canonical schema.

    Canonical fields:
      - relevancy_score (0-100 int)
      - relevancy_reason (str)
      - confidence (low/medium/high)
      - signals (dict)
      - summary (str)
      - concerns (list)
    """
    data = dict(raw)
    version = (prompt_version or "").lower()

    if "relevancy_score" not in data:
        if "relevancy_score_0_100" in data:
            data["relevancy_score"] = data.get("relevancy_score_0_100")

    if "relevancy_reason" not in data:
        if isinstance(data.get("key_reasons"), list):
            data["relevancy_reason"] = "; ".join([str(r) for r in data.get("key_reasons")])
        elif isinstance(data.get("summary"), str):
            data["relevancy_reason"] = data.get("summary")

    if "confidence" not in data:
        if "uncertainty" in data:
            data["confidence"] = _confidence_from_uncertainty(data.get("uncertainty"))
        else:
            data["confidence"] = "medium"

    if "signals" not in data:
        data["signals"] = {}

    if "summary" not in data:
        data["summary"] = ""

    concerns = data.get("concerns")
    if concerns is None or concerns == "" or (isinstance(concerns, str) and concerns.strip().lower() == "none"):
        data["concerns"] = []
    elif isinstance(concerns, list):
        cleaned = []
        for c in concerns:
            if c is None:
                continue
            text = str(c).strip()
            if text:
                cleaned.append(text)
        data["concerns"] = cleaned
    elif isinstance(concerns, str):
        text = concerns.strip()
        data["concerns"] = [text] if text else []
    else:
        data["concerns"] = [str(concerns)]

    if version.startswith("v1"):
        if "relevancy_score" not in data and "relevancy_rating_0_3" in data:
            try:
                rating = int(data.get("relevancy_rating_0_3"))
                data["relevancy_score"] = max(0, min(100, rating * 33))
            except (TypeError, ValueError):
                pass

    return data
