"""AI-powered reranker for must-reads using OpenAI API.

This module provides optional LLM-based reranking of publications
with strict fallback to heuristic ordering.
"""

import json
import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

# OpenAI model for reranking
DEFAULT_MODEL = "gpt-4o-mini"
MAX_TEXT_SNIPPET = 1200  # Max characters from raw_text (increased for better context)


def _prepare_rerank_input(publications: List[dict]) -> List[dict]:
    """Prepare publications for reranking with safe truncation.

    Args:
        publications: List of publication dicts with heuristic scores

    Returns:
        List of minimal publication dicts for LLM input with confidence indicators
    """
    rerank_input = []
    for pub in publications:
        # Prepare text snippet with priority: summary > raw_text > empty
        text_snippet = ""
        has_summary = False
        has_raw_text = False

        if pub.get("summary") and pub["summary"] != "No summary available.":
            text_snippet = pub["summary"][:MAX_TEXT_SNIPPET]
            has_summary = True
        elif pub.get("raw_text"):
            text_snippet = pub["raw_text"][:MAX_TEXT_SNIPPET]
            has_raw_text = True

        # Determine confidence based on available data
        if has_summary or (has_raw_text and len(pub.get("raw_text", "")) > 300):
            confidence = "high"
        elif has_raw_text:
            confidence = "medium"
        else:
            confidence = "low"

        rerank_input.append(
            {
                "id": pub.get("id", ""),
                "title": pub.get("title", ""),
                "venue": pub.get("venue", ""),
                "source": pub.get("source", ""),
                "published_date": pub.get("published_date", ""),
                "url": pub.get("url", ""),
                "text_snippet": text_snippet,
                "has_abstract": has_summary or has_raw_text,
            }
        )
    return rerank_input


def _build_rerank_prompt(publications: List[dict]) -> str:
    """Build the improved reranking prompt for OpenAI with SpotItEarly rubric.

    Args:
        publications: List of publication dicts

    Returns:
        Prompt string with strict scoring rubric
    """
    pub_summaries = []
    for i, pub in enumerate(publications, 1):
        abstract_indicator = " [Has abstract]" if pub.get('has_abstract') else " [Title/venue only]"
        pub_summaries.append(
            f"{i}. ID: {pub['id']}{abstract_indicator}\n"
            f"   Title: {pub['title']}\n"
            f"   Venue: {pub['venue']}\n"
            f"   Source: {pub['source']}\n"
            f"   Date: {pub['published_date']}\n"
            f"   URL: {pub.get('url', 'N/A')}\n"
            f"   Text: {pub['text_snippet'][:400] if pub['text_snippet'] else 'Not available'}\n"
        )

    publications_text = "\n".join(pub_summaries)

    prompt = f"""You are an expert evaluator for SpotItEarly, a company focused on early cancer detection and screening innovation.

Your task: Rank {len(publications)} publications by relevance to SpotItEarly's mission using this rubric:

**SCORING RUBRIC (0-100):**

HIGH PRIORITY (70-100 points):
- Early cancer detection/screening methods (liquid biopsy, imaging, biomarkers)
- Novel diagnostic biomarkers with clinical validation
- Prospective clinical trials for early-stage cancer detection
- Cell-free DNA, ctDNA, methylation-based detection
- Multi-cancer early detection (MCED) technologies
- Screening effectiveness studies with strong evidence (sensitivity/specificity data)
- Translational/commercial potential for real-world deployment

MEDIUM PRIORITY (40-69 points):
- Cancer biology relevant to early detection mechanisms
- Biomarker discovery (pre-clinical or small cohorts)
- Retrospective studies on early detection methods
- Risk stratification or prediction models
- Minimal residual disease (MRD) monitoring

LOW PRIORITY (10-39 points):
- Treatment-focused (immunotherapy, chemotherapy, surgery) without detection angle
- Late-stage cancer research
- Cancer prevention (lifestyle, diet) without diagnostic innovation
- General cancer epidemiology

DEPRIORITIZE (0-9 points):
- Non-cancer biology (plant biology, ecology, veterinary, non-human)
- Unrelated medical fields (cardiology, neurology without cancer connection)
- Pure methodology papers without cancer application
- Editorials, letters, non-research articles

**EVIDENCE STRENGTH MULTIPLIERS:**
- Large prospective cohort (>1000 patients): +10 points
- Clinical validation data (sensitivity/specificity): +10 points
- FDA/regulatory pathway mentioned: +5 points
- Multi-center trial: +5 points

**CRITICAL RULES:**
1. DO NOT HALLUCINATE: If abstract/text is missing or findings are unclear, state "Not enough information" in findings.
2. If title/venue suggests irrelevant domain (e.g., plant biology, animal ecology), score 0-5 regardless of keywords.
3. Recency bonus: Publications from last 30 days get +5 points.
4. Confidence:
   - "high" if abstract available and findings clearly stated
   - "medium" if title/venue only but domain is relevant
   - "low" if title-only and unclear relevance

**OUTPUT FORMAT (STRICT JSON):**

Publications:
{publications_text}

Return ONLY valid JSON array (no markdown, no extra text):
[
  {{
    "pub_id": "exact_id_from_above",
    "llm_score": 85,
    "llm_rank": 1,
    "llm_reason": "Novel ctDNA methylation assay with 92% sensitivity for stage I lung cancer",
    "llm_why_it_matters": "This study validates a multi-cancer early detection platform in a prospective cohort of 10,000 patients, demonstrating clinical utility for population screening.",
    "llm_key_findings": ["Sensitivity: 92% for stage I cancers", "Specificity: 96% in validation cohort", "Cost: $500 per test"],
    "llm_tags": ["liquid biopsy", "ctDNA", "early detection", "screening", "clinical validation", "lung cancer"],
    "confidence": "high"
  }}
]

IMPORTANT:
- Include ALL {len(publications)} publications
- Scores 0-100
- Unique ranks 1..N
- Tags: 0-6 relevant tags from: biomarker, screening, early detection, ctDNA, methylation, liquid biopsy, clinical trial, FDA, MCED, imaging, sensitivity, specificity
- If findings not in text, use empty array [] or ["Not enough information"]
- Confidence: high/medium/low based on available data
"""
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

        # Call OpenAI API with low temperature for deterministic behavior
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert evaluator for SpotItEarly, focused on early cancer detection and screening. Return ONLY valid JSON with no additional text or markdown.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,  # Low temperature for consistent, deterministic output
            max_tokens=6000,  # Increased for more detailed responses with tags
        )

        # Parse response
        response_text = response.choices[0].message.content.strip()

        # Remove markdown code blocks if present
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(
                line for line in lines if not line.startswith("```")
            )

        # Parse JSON
        rerank_results = json.loads(response_text)

        if not isinstance(rerank_results, list):
            logger.error("OpenAI response is not a list: %s", response_text[:200])
            return None

        logger.info(
            "Successfully reranked %d publications with OpenAI", len(rerank_results)
        )
        return rerank_results

    except ImportError:
        logger.warning("OpenAI library not installed, skipping LLM rerank")
        return None
    except json.JSONDecodeError as e:
        logger.error("Failed to parse OpenAI JSON response: %s", e)
        logger.debug("Response text: %s", response_text[:500])
        return None
    except Exception as e:
        logger.error("OpenAI API call failed: %s", e)
        return None


def merge_rerank_results(
    publications: List[dict], rerank_results: List[dict]
) -> List[dict]:
    """Merge rerank results back into publications with new structured fields.

    Args:
        publications: Original publications with heuristic scores
        rerank_results: Rerank results from OpenAI with tags and confidence

    Returns:
        Publications with merged llm_score, llm_rank, llm_reason, tags, confidence, etc.
    """
    # Create lookup dict - handle both "id" and "pub_id" keys
    rerank_lookup = {}
    for r in rerank_results:
        if "pub_id" in r:
            rerank_lookup[r["pub_id"]] = r
        elif "id" in r:
            rerank_lookup[r["id"]] = r

    # Merge results
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
            # No rerank data for this publication
            logger.warning("No rerank data for pub_id=%s", pub_id)
            pub["llm_score"] = 0
            pub["llm_rank"] = 999
            pub["llm_reason"] = ""
            pub["llm_why"] = ""
            pub["llm_findings"] = []
            pub["llm_tags"] = []
            pub["llm_confidence"] = "low"

        merged.append(pub)

    return merged
