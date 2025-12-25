"""Generate summaries for publications."""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from acitrack_types import Publication

logger = logging.getLogger(__name__)

# Summarization prompt template
SUMMARY_PROMPT_TEMPLATE = """You are summarizing an academic or technical publication.

Title:
{title}

Source:
{source}

Text:
{raw_text}

Task:
1) Write 3–5 concise bullet points capturing the core contribution or finding.
2) Write one neutral sentence describing what the work investigates or reports.

Constraints:
- Do not speculate beyond the text.
- Do not evaluate importance or quality.
- Be precise and conservative.

Please respond with a JSON object in this format:
{{
  "essence_bullets": ["bullet 1", "bullet 2", "bullet 3"],
  "one_liner": "One sentence description."
}}
"""


def load_cached_summary(summary_dir: str, publication_id: str) -> Optional[dict]:
    """Load cached summary from disk.

    Args:
        summary_dir: Directory containing cached summaries
        publication_id: Publication ID

    Returns:
        Cached summary dict or None if not found
    """
    summary_path = Path(summary_dir) / f"{publication_id}.json"
    if not summary_path.exists():
        return None

    try:
        with open(summary_path, "r") as f:
            summary = json.load(f)
        logger.debug("Loaded cached summary for publication %s", publication_id)
        return summary
    except Exception as e:
        logger.warning("Failed to load cached summary for %s: %s", publication_id, e)
        return None


def save_summary_cache(summary_dir: str, publication_id: str, summary: dict) -> None:
    """Save summary to cache.

    Args:
        summary_dir: Directory to save cached summaries
        publication_id: Publication ID
        summary: Summary dict to save
    """
    summary_path = Path(summary_dir) / f"{publication_id}.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)
        logger.debug("Cached summary for publication %s", publication_id)
    except Exception as e:
        logger.warning("Failed to cache summary for %s: %s", publication_id, e)


def summarize_text_openai(title: str, source: str, raw_text: str) -> dict:
    """Summarize text using OpenAI API.

    Args:
        title: Publication title
        source: Publication source
        raw_text: Raw text to summarize

    Returns:
        Dictionary with essence_bullets and one_liner
    """
    try:
        import openai

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not found in environment")
            return {
                "essence_bullets": [],
                "one_liner": "Summary unavailable (missing API key)."
            }

        client = openai.OpenAI(api_key=api_key)

        prompt = SUMMARY_PROMPT_TEMPLATE.format(
            title=title,
            source=source,
            raw_text=raw_text[:2000]  # Limit text length
        )

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that summarizes academic publications."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=500
        )

        content = response.choices[0].message.content

        # Try to parse JSON from response
        try:
            # Remove markdown code blocks if present
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            summary = json.loads(content)
            return {
                "essence_bullets": summary.get("essence_bullets", []),
                "one_liner": summary.get("one_liner", "Summary unavailable.")
            }
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse JSON from OpenAI response: %s", e)
            return {
                "essence_bullets": [],
                "one_liner": "Summary unavailable (parse error)."
            }

    except ImportError:
        logger.warning("openai package not installed")
        return {
            "essence_bullets": [],
            "one_liner": "Summary unavailable (openai not installed)."
        }
    except Exception as e:
        logger.warning("OpenAI API call failed: %s", e)
        return {
            "essence_bullets": [],
            "one_liner": "Summary unavailable."
        }


def summarize_text(title: str, source: str, raw_text: str) -> dict:
    """Summarize text using configured LLM provider.

    Args:
        title: Publication title
        source: Publication source
        raw_text: Raw text to summarize

    Returns:
        Dictionary with essence_bullets and one_liner
    """
    provider = os.environ.get("ACITRACK_LLM_PROVIDER", "").lower()

    if provider == "openai":
        return summarize_text_openai(title, source, raw_text)
    elif provider == "claude":
        # TODO: Implement Claude API integration
        logger.info("Claude provider not yet implemented")
        return {
            "essence_bullets": [],
            "one_liner": "Summary unavailable (Claude provider not implemented)."
        }
    else:
        # Default: stub implementation
        logger.debug("No LLM provider configured, using stub summary")
        return {
            "essence_bullets": [
                "This is a placeholder summary.",
                "Set ACITRACK_LLM_PROVIDER to enable real summaries."
            ],
            "one_liner": "No LLM provider configured."
        }


def summarize_publications(
    publications: list[Publication],
    new_publication_ids: set[str],
    summary_dir: str
) -> dict[str, dict]:
    """Generate summaries for NEW publications only.

    Args:
        publications: List of all publications
        new_publication_ids: Set of IDs for NEW publications
        summary_dir: Directory for cached summaries

    Returns:
        Dictionary mapping publication_id to summary dict
    """
    logger.info("Summarizing %d NEW publications", len(new_publication_ids))

    summaries = {}
    cache_hits = 0
    cache_misses = 0

    for pub in publications:
        if pub.id not in new_publication_ids:
            continue

        # Try to load from cache
        cached_summary = load_cached_summary(summary_dir, pub.id)
        if cached_summary:
            summaries[pub.id] = cached_summary
            cache_hits += 1
            continue

        # Generate new summary
        cache_misses += 1
        logger.info("Generating summary for: %s", pub.title)

        try:
            summary = summarize_text(pub.title, pub.source, pub.raw_text)
            summaries[pub.id] = summary

            # Save to cache
            save_summary_cache(summary_dir, pub.id, summary)

        except Exception as e:
            logger.warning("Failed to summarize publication %s: %s", pub.id, e)
            summaries[pub.id] = {
                "essence_bullets": [],
                "one_liner": "Summary unavailable."
            }

    logger.info(
        "Summary generation complete - Cache hits: %d, Cache misses: %d",
        cache_hits,
        cache_misses
    )

    return summaries
