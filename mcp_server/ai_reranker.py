"""AI-powered reranker for must-reads using OpenAI API.

This module provides optional LLM-based reranking of publications
with strict fallback to heuristic ordering.
"""

import difflib
import json
import logging
import os
import re
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)

# OpenAI model for reranking
DEFAULT_MODEL = "gpt-4o-mini"
MAX_TEXT_SNIPPET = 300  # Reduced to minimize token usage and prevent truncation
MAX_RERANK_CANDIDATES = 25  # Reduced from 50 to ensure reliable parsing


def _prepare_rerank_input(publications: List[dict]) -> List[dict]:
    """Prepare publications for reranking with MINIMAL data to prevent truncation.

    Args:
        publications: List of publication dicts with heuristic scores

    Returns:
        List of minimal publication dicts for LLM input (id, title, source only)
    """
    rerank_input = []
    for pub in publications:
        # Get text snippet - prefer summary, fallback to raw_text
        text_snippet = ""
        if pub.get("summary") and pub["summary"] != "No summary available.":
            text_snippet = pub["summary"][:MAX_TEXT_SNIPPET]
        elif pub.get("raw_text"):
            text_snippet = pub["raw_text"][:MAX_TEXT_SNIPPET]

        # Minimal input to reduce tokens
        rerank_input.append(
            {
                "id": pub.get("id", ""),
                "title": pub.get("title", "")[:250],  # Truncate long titles
                "source": pub.get("source", ""),
                "text": text_snippet,  # Short snippet only
            }
        )
    return rerank_input


def _build_rerank_prompt(publications: List[dict]) -> str:
    """Build MINIMAL reranking prompt requesting only ranked IDs.

    Args:
        publications: List of publication dicts (id, title, source, text)

    Returns:
        Concise prompt requesting JSON array of ranked IDs
    """
    pub_list = []
    for i, pub in enumerate(publications, 1):
        text_preview = pub.get('text', '')[:200] if pub.get('text') else '[No abstract]'
        pub_list.append(
            f"{i}. {pub['id']}\n"
            f"   Title: {pub['title']}\n"
            f"   Source: {pub['source']}\n"
            f"   Text: {text_preview}\n"
        )

    publications_text = "\n".join(pub_list)

    prompt = f"""Rank these {len(publications)} cancer research publications by relevance to early cancer detection and screening.

SpotItEarly Priorities:
• HIGH: Early detection methods (liquid biopsy, ctDNA, methylation, biomarkers, MCED, screening trials)
• MEDIUM: Cancer biology, biomarker discovery, risk models
• LOW: Treatment-only research, late-stage cancer, prevention without diagnostics
• SKIP: Non-cancer biology, unrelated medical fields

Publications:
{publications_text}

Return ONLY this JSON (no markdown, no explanation):
{{
  "ranked_ids": ["id_of_most_relevant", "id_of_second", ..., "id_of_least_relevant"]
}}

Rules:
- Include ALL {len(publications)} IDs in ranked_ids array
- IDs must be EXACT strings from above (copy-paste)
- Order from most to least relevant
- No extra fields, no explanations"""
    return prompt


def rerank_with_openai(
    publications: List[dict],
    model: str = DEFAULT_MODEL,
    api_key: Optional[str] = None,
) -> Optional[List[dict]]:
    """Rerank publications using OpenAI API.

    Args:
        publications: List of publication dicts with heuristic scores
        model: OpenAI model name (default: gpt-4o-mini)
        api_key: OpenAI API key (if None, uses OPENAI_API_KEY env var)

    Returns:
        List of reranked publications with llm_score, llm_rank, etc.
        Returns None if API call fails or key is missing.
    """
    # Check for API key
    if api_key is None:
        api_key = os.environ.get("OPENAI_API_KEY")

    if not api_key:
        logger.info("OPENAI_API_KEY not found, skipping LLM rerank")
        return None

    try:
        # Import OpenAI (only when needed)
        from openai import OpenAI

        client = OpenAI(api_key=api_key)

        # Prepare input
        rerank_input = _prepare_rerank_input(publications)
        prompt = _build_rerank_prompt(rerank_input)

        logger.info(
            "Calling OpenAI API (%s) to rerank %d publications",
            model,
            len(publications),
        )

        # Call OpenAI API with strict JSON mode and minimal tokens
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a cancer research expert. Return ONLY valid JSON with no markdown.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=800,  # Minimal: 25 IDs * ~30 chars + overhead
            response_format={"type": "json_object"},
        )

        # Parse response
        response_text = response.choices[0].message.content.strip()

        # Try to parse the minimal JSON response
        try:
            parsed = json.loads(response_text)
            ranked_ids = parsed.get("ranked_ids", [])

            if not ranked_ids or not isinstance(ranked_ids, list):
                raise ValueError("Response missing 'ranked_ids' array")

            # Convert minimal response to full format for backward compatibility
            rerank_results = _convert_ranked_ids_to_results(
                ranked_ids, publications
            )

            logger.info(
                "Successfully reranked %d publications with OpenAI",
                len(rerank_results),
            )
            return rerank_results

        except (json.JSONDecodeError, ValueError) as e:
            # RETRY ONCE with repair prompt
            logger.warning(
                "Failed to parse OpenAI response (attempt 1/2): %s", e
            )
            logger.warning(
                "Response preview: %s...", response_text[:300]
            )
            logger.info("Retrying with repair prompt")

            repair_prompt = f"""Your previous output was invalid. Return ONLY this JSON:
{{
  "ranked_ids": ["exact_id_1", "exact_id_2", ...]
}}

Include all {len(publications)} IDs from the original list in ranked order."""

            retry_response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Return ONLY valid JSON."},
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": response_text},
                    {"role": "user", "content": repair_prompt},
                ],
                temperature=0.0,
                max_tokens=800,
                response_format={"type": "json_object"},
            )

            retry_text = retry_response.choices[0].message.content.strip()

            try:
                parsed = json.loads(retry_text)
                ranked_ids = parsed.get("ranked_ids", [])

                if not ranked_ids:
                    raise ValueError("Retry missing 'ranked_ids'")

                rerank_results = _convert_ranked_ids_to_results(
                    ranked_ids, publications
                )

                logger.info("Successfully parsed on retry (attempt 2/2)")
                return rerank_results

            except (json.JSONDecodeError, ValueError) as retry_error:
                logger.error(
                    "Failed on retry (attempt 2/2): %s", retry_error
                )
                logger.error("Response: %s...", retry_text[:300])
                logger.error("Falling back to heuristic ranking")
                return None

    except ImportError:
        logger.warning("OpenAI library not installed, skipping LLM rerank")
        return None
    except Exception as e:
        logger.error("OpenAI API call failed: %s", e)
        return None


def _convert_ranked_ids_to_results(
    ranked_ids: List[str], publications: List[dict]
) -> List[dict]:
    """Convert minimal ranked_ids response to full results format.

    Args:
        ranked_ids: List of publication IDs in ranked order
        publications: Original publication dicts with full data

    Returns:
        List of results with llm_score, llm_rank for backward compatibility
    """
    # Build lookup by ID
    pub_by_id = {pub["id"]: pub for pub in publications}

    # Convert to full format
    results = []
    for rank, pub_id in enumerate(ranked_ids, 1):
        if pub_id not in pub_by_id:
            logger.warning("Ranked ID not in original list: %s", pub_id[:16])
            continue

        # Score: inverse of rank (higher rank = lower score)
        # Map rank 1 → 100, rank 25 → 4, etc.
        llm_score = max(4, int(100 - (rank - 1) * 4))

        results.append({
            "pub_id": pub_id,
            "id": pub_id,  # Backward compatibility
            "title": pub_by_id[pub_id].get("title", ""),
            "llm_score": llm_score,
            "llm_rank": rank,
            "llm_reason": "",  # Not provided in minimal response
            "llm_why_it_matters": "",  # Will be filled by summary step
            "llm_key_findings": [],
            "llm_tags": [],
            "confidence": "medium",  # Default for minimal response
        })

    return results


def _normalize_title(title: str) -> str:
    """Normalize title for comparison (lowercase, remove extra whitespace/punctuation).

    Args:
        title: Original title string

    Returns:
        Normalized title for fuzzy matching
    """
    # Lowercase and strip
    normalized = title.lower().strip()
    # Remove multiple spaces
    normalized = re.sub(r'\s+', ' ', normalized)
    # Remove common punctuation but keep alphanumeric and spaces
    normalized = re.sub(r'[^\w\s-]', '', normalized)
    return normalized


def _validate_rerank_item(
    item: dict,
    candidates_by_id: Dict[str, dict],
    min_title_similarity: float = 0.92
) -> tuple[bool, str]:
    """Validate a single rerank result item.

    Args:
        item: Rerank result dict from LLM
        candidates_by_id: Dict mapping pub_id -> publication data
        min_title_similarity: Minimum fuzzy match ratio (0-1) for title validation

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check if pub_id exists
    pub_id = item.get("pub_id")
    if not pub_id:
        return False, "Missing pub_id field"

    if pub_id not in candidates_by_id:
        return False, f"Unknown pub_id: {pub_id}"

    # Get candidate
    candidate = candidates_by_id[pub_id]
    expected_title = candidate.get("title", "")
    returned_title = item.get("title", "")

    # Check if title exists in response
    if not returned_title:
        return False, f"Missing title field for pub_id={pub_id}"

    # Normalize titles
    expected_norm = _normalize_title(expected_title)
    returned_norm = _normalize_title(returned_title)

    # Exact match after normalization
    if expected_norm == returned_norm:
        return True, ""

    # Fuzzy match using difflib
    similarity = difflib.SequenceMatcher(None, expected_norm, returned_norm).ratio()

    if similarity >= min_title_similarity:
        return True, ""

    # Validation failed
    error_msg = (
        f"Title mismatch for pub_id={pub_id[:16]}...: "
        f"expected='{expected_title[:50]}...' "
        f"got='{returned_title[:50]}...' "
        f"(similarity={similarity:.2f}, threshold={min_title_similarity})"
    )
    return False, error_msg


def merge_rerank_results(
    publications: List[dict], rerank_results: List[dict]
) -> tuple[List[dict], List[dict]]:
    """Merge rerank results back into publications with validation.

    This function validates each rerank result against the original publication
    to prevent cross-wired LLM outputs. Only validated results are applied.

    Args:
        publications: Original publications with heuristic scores
        rerank_results: Rerank results from OpenAI with tags and confidence

    Returns:
        Tuple of (merged_publications, validated_rerank_items):
        - merged_publications: Publications with merged LLM data (validated only)
        - validated_rerank_items: List of validated rerank items (for caching)
        Publications that fail validation fall back to heuristic-only mode.
    """
    # Build candidates_by_id lookup for validation
    candidates_by_id = {pub["id"]: pub for pub in publications}

    # Validate and build rerank lookup
    rerank_lookup = {}
    duplicate_count = 0
    dropped_count = 0

    for item in rerank_results:
        # Normalize pub_id field (handle both "pub_id" and "id")
        pub_id = item.get("pub_id") or item.get("id")

        # Validate this item
        is_valid, error_msg = _validate_rerank_item(item, candidates_by_id)

        if not is_valid:
            dropped_count += 1
            logger.warning("LLM rerank dropped (validation failed): %s", error_msg)
            continue

        # Check for duplicates (keep higher score)
        if pub_id in rerank_lookup:
            duplicate_count += 1
            existing_score = rerank_lookup[pub_id].get("llm_score", 0)
            new_score = item.get("llm_score", 0)
            if new_score > existing_score:
                logger.warning(
                    "LLM rerank duplicate pub_id=%s (keeping higher score: %d > %d)",
                    pub_id[:16], new_score, existing_score
                )
                rerank_lookup[pub_id] = item
            else:
                logger.warning(
                    "LLM rerank duplicate pub_id=%s (keeping existing score: %d >= %d)",
                    pub_id[:16], existing_score, new_score
                )
            continue

        # Valid and unique - accept it
        rerank_lookup[pub_id] = item
        logger.info(
            "LLM rerank accepted: pub_id=%s score=%d rank=%d",
            pub_id[:16], item.get("llm_score", 0), item.get("llm_rank", 0)
        )

    # Log summary
    if dropped_count > 0 or duplicate_count > 0:
        logger.warning(
            "LLM rerank validation summary: %d accepted, %d dropped (validation), %d duplicates",
            len(rerank_lookup), dropped_count, duplicate_count
        )

    # Merge validated results into publications
    merged = []
    for pub in publications:
        pub_id = pub.get("id")
        if pub_id and pub_id in rerank_lookup:
            llm_data = rerank_lookup[pub_id]
            pub["llm_score"] = llm_data.get("llm_score", 0)
            pub["llm_rank"] = llm_data.get("llm_rank", 0)
            pub["llm_reason"] = llm_data.get("llm_reason", "")
            # Handle both old and new field names
            pub["llm_why"] = llm_data.get("llm_why_it_matters", llm_data.get("llm_why", ""))
            pub["llm_findings"] = llm_data.get("llm_key_findings", llm_data.get("llm_findings", []))
            # New structured fields
            pub["llm_tags"] = llm_data.get("llm_tags", [])
            pub["llm_confidence"] = llm_data.get("confidence", "medium")
        else:
            # No validated rerank data - use heuristic-only mode
            pub["llm_score"] = 0
            pub["llm_rank"] = 999
            pub["llm_reason"] = ""
            pub["llm_why"] = ""
            pub["llm_findings"] = []
            pub["llm_tags"] = []
            pub["llm_confidence"] = "low"

        merged.append(pub)

    # Return both merged pubs and validated items (for caching)
    validated_items = list(rerank_lookup.values())
    return merged, validated_items
