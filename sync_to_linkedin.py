"""Sync high-scoring Science Agent publications to LinkedIn Manager's fact_bank.

Runs after the daily tri-model pipeline. Queries the local publications table for
recent high-relevancy articles and upserts them into the LinkedIn Manager Supabase
fact_bank via PostgREST API.

Required env vars:
  DATABASE_URL                       - Science Agent PostgreSQL connection string
  LINKEDIN_MANAGER_SUPABASE_URL      - LinkedIn Manager Supabase project URL
  LINKEDIN_MANAGER_SUPABASE_SERVICE_KEY - LinkedIn Manager Supabase service role key
"""

import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─── Configuration ───────────────────────────────────────────

MIN_RELEVANCY_SCORE = 80      # Only sync High tier publications
LOOKBACK_DAYS = 30             # How far back to look for publications
MAX_FACT_TEXT_CHARS = 600      # Truncate fact text to fit token budget
SOURCE_NAME = "science_agent"  # Identifies synced facts in fact_bank

# Domain-specific tag keywords to extract from titles/summaries
TAG_KEYWORDS = {
    "cancer-research": ["cancer", "tumor", "tumour", "oncol", "carcinoma", "neoplasm", "malignant"],
    "breath-analysis": ["breath", "voc", "volatile organic", "exhaled"],
    "early-detection": ["early detection", "early diagnosis", "screening", "early-stage"],
    "diagnostics": ["diagnostic", "biomarker", "biosensor", "point-of-care"],
    "liquid-biopsy": ["liquid biopsy", "ctdna", "cell-free", "circulating tumor"],
    "clinical-trial": ["clinical trial", "clinical study", "phase i", "phase ii", "phase iii", "randomized"],
    "ai-ml": ["machine learning", "deep learning", "artificial intelligence", "neural network"],
}


def derive_tags(title: str, summary: str, venue: str) -> List[str]:
    """Derive topic tags from publication metadata."""
    tags = ["scientific"]
    text = f"{title} {summary}".lower()

    for tag, keywords in TAG_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            tags.append(tag)

    # Add venue as a tag if it's a recognizable journal
    if venue:
        venue_lower = venue.lower().strip()
        # Clean common suffixes
        for suffix in [" journal", " reviews", " communications"]:
            venue_lower = venue_lower.replace(suffix, "")
        venue_tag = re.sub(r"[^a-z0-9]+", "-", venue_lower).strip("-")
        if venue_tag and len(venue_tag) <= 30:
            tags.append(venue_tag)

    tags.append("high-relevancy")
    return tags


def build_fact_text(summary: str, relevancy_reason: str) -> str:
    """Build a concise fact text from the publication summary and relevancy reason."""
    # Prefer the summary (which is the AI-generated final_summary)
    text = summary or relevancy_reason or ""
    text = text.strip()
    if len(text) > MAX_FACT_TEXT_CHARS:
        # Truncate at last sentence boundary within limit
        truncated = text[:MAX_FACT_TEXT_CHARS]
        last_period = truncated.rfind(".")
        if last_period > MAX_FACT_TEXT_CHARS // 2:
            text = truncated[: last_period + 1]
        else:
            text = truncated.rstrip() + "..."
    return text


def build_link(pub: Dict) -> Optional[str]:
    """Build URL using the same fallback chain as Science Agent digest."""
    if pub.get("canonical_url"):
        return pub["canonical_url"]
    if pub.get("url"):
        return pub["url"]
    if pub.get("doi"):
        return f"https://doi.org/{pub['doi']}"
    if pub.get("pmid"):
        return f"https://pubmed.ncbi.nlm.nih.gov/{pub['pmid']}/"
    return None


def fetch_high_scoring_publications(database_url: str) -> List[Dict]:
    """Query Science Agent DB for recent high-relevancy publications."""
    cutoff = (datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    conn = psycopg2.connect(database_url)
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Detect available columns
        cursor.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = 'publications'"""
        )
        available = {row["column_name"] for row in cursor.fetchall()}

        # Detect PK column
        pk = next((c for c in ("id", "publication_id", "pub_id") if c in available), None)
        if not pk:
            logger.error("Cannot find primary key column in publications table")
            return []

        # Build select list from available columns
        desired = [
            pk, "title", "authors", "venue", "source",
            "published_date", "canonical_url", "url", "doi", "pmid",
            "final_relevancy_score", "final_summary", "final_relevancy_reason",
            "credibility_score", "agreement_level",
        ]
        select_cols = [c for c in desired if c in available]

        query = f"""
            SELECT {", ".join(select_cols)}
            FROM publications
            WHERE final_relevancy_score >= %s
              AND published_date >= %s
            ORDER BY final_relevancy_score DESC, published_date DESC
        """
        cursor.execute(query, (MIN_RELEVANCY_SCORE, cutoff))
        rows = cursor.fetchall()
        logger.info(f"Found {len(rows)} publications with score >= {MIN_RELEVANCY_SCORE} since {cutoff}")

        # Normalize PK to 'publication_id'
        result = []
        for row in rows:
            d = dict(row)
            if pk != "publication_id":
                d["publication_id"] = d.pop(pk)
            result.append(d)
        return result
    finally:
        conn.close()


def transform_to_facts(publications: List[Dict]) -> List[Dict]:
    """Transform publications into fact_bank rows."""
    facts = []
    for pub in publications:
        link = build_link(pub)
        if not link:
            logger.warning(f"Skipping publication with no URL: {pub.get('title', 'unknown')}")
            continue

        title = pub.get("title", "").strip()
        if not title:
            continue

        summary = pub.get("final_summary", "") or ""
        reason = pub.get("final_relevancy_reason", "") or ""
        venue = pub.get("venue", "") or pub.get("source", "") or ""
        authors = pub.get("authors", "") or ""

        fact_text = build_fact_text(summary, reason)
        if not fact_text:
            logger.warning(f"Skipping publication with no summary: {title}")
            continue

        tags = derive_tags(title, summary, venue)

        # Build description with metadata
        desc_parts = []
        if venue:
            desc_parts.append(venue)
        if authors:
            # Truncate long author lists
            author_str = authors if len(authors) <= 80 else authors[:77] + "..."
            desc_parts.append(author_str)
        score = pub.get("final_relevancy_score")
        if score is not None:
            desc_parts.append(f"Relevancy: {score}/100")

        facts.append({
            "text": fact_text,
            "link_url": link,
            "link_title": title[:300] if len(title) > 300 else title,
            "link_description": " | ".join(desc_parts) if desc_parts else None,
            "tags": tags,
            "source": SOURCE_NAME,
            "source_id": pub["publication_id"],
        })

    return facts


def upsert_to_supabase(facts: List[Dict], supabase_url: str, service_key: str) -> int:
    """Upsert facts into LinkedIn Manager's fact_bank via PostgREST."""
    if not facts:
        logger.info("No facts to sync.")
        return 0

    endpoint = f"{supabase_url}/rest/v1/fact_bank"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }

    # Batch upsert (PostgREST handles the unique constraint on source+source_id)
    response = requests.post(endpoint, headers=headers, json=facts)

    if response.status_code in (200, 201):
        logger.info(f"Successfully synced {len(facts)} facts to LinkedIn Manager fact_bank.")
        return len(facts)
    else:
        logger.error(f"Supabase upsert failed ({response.status_code}): {response.text}")
        return 0


def main():
    database_url = os.environ.get("DATABASE_URL")
    supabase_url = os.environ.get("LINKEDIN_MANAGER_SUPABASE_URL")
    service_key = os.environ.get("LINKEDIN_MANAGER_SUPABASE_SERVICE_KEY")

    if not database_url:
        logger.error("DATABASE_URL not set. Cannot query Science Agent database.")
        sys.exit(1)

    if not supabase_url or not service_key:
        logger.warning("LinkedIn Manager Supabase credentials not set. Skipping sync.")
        sys.exit(0)

    publications = fetch_high_scoring_publications(database_url)
    if not publications:
        logger.info("No high-scoring publications found to sync.")
        return

    facts = transform_to_facts(publications)
    logger.info(f"Transformed {len(facts)} publications into fact_bank entries.")

    synced = upsert_to_supabase(facts, supabase_url, service_key)
    logger.info(f"Sync complete: {synced}/{len(facts)} facts upserted.")


if __name__ == "__main__":
    main()
