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
MAX_TEXT_SNIPPET = 800  # Max characters from raw_text


def _prepare_rerank_input(publications: List[dict]) -> List[dict]:
    """Prepare publications for reranking.

    Args:
        publications: List of publication dicts with heuristic scores

    Returns:
        List of minimal publication dicts for LLM input
    """
    rerank_input = []
    for pub in publications:
        # Prepare text snippet
        text_snippet = ""
        if pub.get("summary"):
            text_snippet = pub["summary"]
        elif pub.get("raw_text"):
            text_snippet = pub["raw_text"][:MAX_TEXT_SNIPPET]

        rerank_input.append(
            {
                "id": pub.get("id", ""),
                "title": pub.get("title", ""),
                "venue": pub.get("venue", ""),
                "source": pub.get("source", ""),
                "published_date": pub.get("published_date", ""),
                "text_snippet": text_snippet,
            }
        )
    return rerank_input


def _build_rerank_prompt(publications: List[dict]) -> str:
    """Build the reranking prompt for OpenAI.

    Args:
        publications: List of publication dicts

    Returns:
        Prompt string
    """
    pub_summaries = []
    for i, pub in enumerate(publications, 1):
        pub_summaries.append(
            f"{i}. ID: {pub['id']}\n"
            f"   Title: {pub['title']}\n"
            f"   Source: {pub['source']} ({pub['venue']})\n"
            f"   Date: {pub['published_date']}\n"
            f"   Text: {pub['text_snippet'][:200]}...\n"
        )

    publications_text = "\n".join(pub_summaries)

    prompt = f"""You are an expert in cancer research, especially early detection and screening.

You are given {len(publications)} recent publications. Your task is to:
1. Score each publication (0-100) based on its relevance to early cancer detection, screening, biomarkers, and diagnostic innovation.
2. Rank them by importance (1 = most important).
3. Provide a brief reason (1 sentence) why this paper matters or doesn't.
4. Write a concise "why it matters" statement (1-2 sentences) for high-scoring papers.
5. Extract 0-3 key findings as bullet points (if available).

Publications:
{publications_text}

Return ONLY a valid JSON array with this exact structure:
[
  {{
    "id": "pub_id",
    "llm_score": 85,
    "llm_rank": 1,
    "llm_reason": "Novel biomarker with high sensitivity",
    "llm_why": "This study demonstrates a breakthrough in early-stage lung cancer detection using cell-free DNA methylation patterns.",
    "llm_findings": ["Sensitivity: 92% for stage I cancers", "Specificity: 96% in validation cohort", "Cost-effective compared to CT screening"]
  }}
]

Important:
- Return ONLY valid JSON (no markdown, no extra text).
- Include all {len(publications)} publications in your response.
- Scores should range from 0-100.
- Ranks should be unique integers starting from 1.
- llm_findings can be an empty array if no clear findings.
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

        # Call OpenAI API
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert in cancer research and early detection. Return only valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=4000,
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
    """Merge rerank results back into publications.

    Args:
        publications: Original publications with heuristic scores
        rerank_results: Rerank results from OpenAI

    Returns:
        Publications with merged llm_score, llm_rank, llm_reason, etc.
    """
    # Create lookup dict
    rerank_lookup = {r["id"]: r for r in rerank_results if "id" in r}

    # Merge results
    merged = []
    for pub in publications:
        pub_id = pub.get("id")
        if pub_id and pub_id in rerank_lookup:
            llm_data = rerank_lookup[pub_id]
            pub["llm_score"] = llm_data.get("llm_score", 0)
            pub["llm_rank"] = llm_data.get("llm_rank", 0)
            pub["llm_reason"] = llm_data.get("llm_reason", "")
            pub["llm_why"] = llm_data.get("llm_why", "")
            pub["llm_findings"] = llm_data.get("llm_findings", [])
        else:
            # No rerank data for this publication
            logger.warning("No rerank data for pub_id=%s", pub_id)
            pub["llm_score"] = 0
            pub["llm_rank"] = 999
            pub["llm_reason"] = ""
            pub["llm_why"] = ""
            pub["llm_findings"] = []

        merged.append(pub)

    return merged
