#!/usr/bin/env python3
"""Export AI summaries for must-reads publications.

This script generates enriched summaries for must-reads using OpenAI:
- why_it_matters (1-2 sentences)
- key_findings (3 bullets)
- study_type (clinical trial, retrospective, etc.)
- evidence_strength (high/medium/low with rationale)

Results are cached in SQLite to minimize API calls.

Usage:
    python3 tools/export_summaries.py [--input PATH] [--output PATH]
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.sqlite_store import _get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Summary version - increment when prompt changes
SUMMARY_VERSION = "v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_DB_PATH = "data/db/acitrack.db"


def get_cached_summary(pub_id: str, summary_version: str, db_path: str) -> Optional[Dict]:
    """Get cached summary for a publication.

    Args:
        pub_id: Publication ID
        summary_version: Summary version string
        db_path: Path to SQLite database

    Returns:
        Dict with summary fields or None if not cached
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT model, why_it_matters, key_findings, study_type,
                   evidence_strength, evidence_rationale
            FROM must_reads_summary_cache
            WHERE pub_id = ? AND summary_version = ?
        """, (pub_id, summary_version))

        row = cursor.fetchone()
        conn.close()

        if row:
            # Parse key_findings from JSON
            key_findings = []
            if row["key_findings"]:
                try:
                    key_findings = json.loads(row["key_findings"])
                except json.JSONDecodeError:
                    logger.warning("Failed to parse key_findings for pub_id=%s", pub_id)

            return {
                "model": row["model"],
                "why_it_matters": row["why_it_matters"] or "",
                "key_findings": key_findings,
                "study_type": row["study_type"] or "",
                "evidence_strength": row["evidence_strength"] or "",
                "evidence_rationale": row["evidence_rationale"] or ""
            }

        return None

    except Exception as e:
        logger.error("Error retrieving cached summary: %s", e)
        return None


def store_summary(pub_id: str, summary: Dict, model: str, summary_version: str, db_path: str) -> bool:
    """Store summary in cache.

    Args:
        pub_id: Publication ID
        summary: Summary dict with fields
        model: Model name used
        summary_version: Summary version string
        db_path: Path to SQLite database

    Returns:
        True if stored successfully
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Serialize key_findings to JSON
        key_findings_json = json.dumps(summary.get("key_findings", []))

        cursor.execute("""
            INSERT OR REPLACE INTO must_reads_summary_cache
            (pub_id, summary_version, model, why_it_matters, key_findings,
             study_type, evidence_strength, evidence_rationale)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pub_id,
            summary_version,
            model,
            summary.get("why_it_matters", ""),
            key_findings_json,
            summary.get("study_type", ""),
            summary.get("evidence_strength", ""),
            summary.get("evidence_rationale", "")
        ))

        conn.commit()
        conn.close()
        return True

    except Exception as e:
        logger.error("Error storing summary: %s", e)
        return False


def generate_summary_with_llm(pub: Dict, model: str = DEFAULT_MODEL) -> Optional[Dict]:
    """Generate summary using OpenAI API.

    Args:
        pub: Publication dict with title, venue, summary/raw_text
        model: OpenAI model name

    Returns:
        Dict with summary fields or None if failed
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set, skipping LLM summary for pub_id=%s", pub.get("id", ""))
        return None

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        # Prepare text for summarization
        title = pub.get("title", "")
        venue = pub.get("venue", "")
        text_snippet = ""

        if pub.get("summary") and pub["summary"] != "No summary available.":
            text_snippet = pub["summary"][:1500]
        elif pub.get("raw_text"):
            text_snippet = pub["raw_text"][:1500]

        prompt = f"""You are an expert evaluator for SpotItEarly, focused on early cancer detection and screening.

Analyze this publication and provide a structured summary:

Title: {title}
Venue: {venue}
Text: {text_snippet if text_snippet else "Not available"}

Provide ONLY valid JSON (no markdown, no extra text):
{{
  "why_it_matters": "1-2 sentence explanation of why this matters for early cancer detection",
  "key_findings": ["Finding 1", "Finding 2", "Finding 3"],
  "study_type": "clinical trial|retrospective|prospective|preprint|review|methods|other",
  "evidence_strength": "high|medium|low",
  "evidence_rationale": "Short explanation of evidence strength"
}}

RULES:
- If text is unavailable, state "Limited information available" in findings
- Focus on early detection relevance
- Be concise and factual
"""

        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert evaluator for SpotItEarly. Return ONLY valid JSON."
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=500
        )

        response_text = response.choices[0].message.content.strip()

        # Remove markdown code blocks if present
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(
                line for line in lines if not line.startswith("```")
            )

        summary = json.loads(response_text)
        logger.info("Generated LLM summary for pub_id=%s", pub.get("id", "")[:16])
        return summary

    except ImportError:
        logger.warning("OpenAI library not installed, skipping LLM summary")
        return None
    except json.JSONDecodeError as e:
        logger.error("Failed to parse LLM summary JSON: %s", e)
        return None
    except Exception as e:
        logger.error("LLM summary generation failed: %s", e)
        return None


def export_summaries(
    input_path: Path = Path("data/output/latest_must_reads.json"),
    output_path: Path = Path("data/output/latest_summaries.json"),
    db_path: str = DEFAULT_DB_PATH
) -> Dict:
    """Export summaries for must-reads publications.

    Args:
        input_path: Path to must-reads JSON file
        output_path: Path to write summaries JSON
        db_path: Path to SQLite database

    Returns:
        Dict with export status
    """
    logger.info("Exporting summaries from: %s", input_path)

    # Trigger schema migration if needed
    _ = _get_connection(db_path)

    # Load must-reads
    if not input_path.exists():
        raise FileNotFoundError(f"Must-reads file not found: {input_path}")

    with open(input_path, "r") as f:
        must_reads_data = json.load(f)

    publications = must_reads_data.get("must_reads", [])
    logger.info("Processing %d publications", len(publications))

    # Generate summaries
    summaries = []
    cached_count = 0
    generated_count = 0
    failed_count = 0

    for pub in publications:
        pub_id = pub.get("id", "")
        if not pub_id:
            logger.warning("Skipping publication without ID")
            failed_count += 1
            continue

        # Check cache first
        cached_summary = get_cached_summary(pub_id, SUMMARY_VERSION, db_path)

        if cached_summary:
            summary_data = cached_summary
            cached_count += 1
        else:
            # Generate with LLM
            llm_summary = generate_summary_with_llm(pub)

            if llm_summary:
                summary_data = llm_summary
                # Store in cache
                store_summary(pub_id, llm_summary, DEFAULT_MODEL, SUMMARY_VERSION, db_path)
                generated_count += 1
            else:
                # Fallback: use existing fields from must-reads
                summary_data = {
                    "why_it_matters": pub.get("why_it_matters", ""),
                    "key_findings": pub.get("key_findings", []),
                    "study_type": "unknown",
                    "evidence_strength": "unknown",
                    "evidence_rationale": "Summary unavailable (no API key)"
                }
                failed_count += 1

        # Add to summaries list
        summaries.append({
            "pub_id": pub_id,
            "title": pub.get("title", ""),
            "source": pub.get("source", ""),
            "venue": pub.get("venue", ""),
            "published_date": pub.get("published_date", ""),
            "url": pub.get("url", ""),
            "why_it_matters": summary_data.get("why_it_matters", ""),
            "key_findings": summary_data.get("key_findings", []),
            "study_type": summary_data.get("study_type", ""),
            "evidence_strength": summary_data.get("evidence_strength", ""),
            "evidence_rationale": summary_data.get("evidence_rationale", "")
        })

    # Write output
    output_data = {
        "generated_at": datetime.now().isoformat(),
        "summary_version": SUMMARY_VERSION,
        "total_count": len(publications),
        "cached_count": cached_count,
        "generated_count": generated_count,
        "failed_count": failed_count,
        "summaries": summaries
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2)

    logger.info("Wrote summaries to: %s", output_path)
    logger.info("Summary stats: cached=%d, generated=%d, failed=%d",
                cached_count, generated_count, failed_count)

    return {
        "success": True,
        "output_path": str(output_path),
        "total_count": len(publications),
        "cached_count": cached_count,
        "generated_count": generated_count,
        "failed_count": failed_count
    }


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Export AI summaries for must-reads publications"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/output/latest_must_reads.json"),
        help="Input must-reads JSON file (default: data/output/latest_must_reads.json)"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/output/latest_summaries.json"),
        help="Output summaries JSON file (default: data/output/latest_summaries.json)"
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=DEFAULT_DB_PATH,
        help=f"SQLite database path (default: {DEFAULT_DB_PATH})"
    )

    args = parser.parse_args()

    try:
        result = export_summaries(
            input_path=args.input,
            output_path=args.output,
            db_path=args.db_path
        )

        print(f"\n✅ Export successful!")
        print(f"   Output: {result['output_path']}")
        print(f"   Total: {result['total_count']}")
        print(f"   Cached: {result['cached_count']}")
        print(f"   Generated: {result['generated_count']}")
        print(f"   Failed: {result['failed_count']}")

        sys.exit(0)

    except Exception as e:
        logger.error("Export failed: %s", e)
        print(f"\n❌ Export failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
