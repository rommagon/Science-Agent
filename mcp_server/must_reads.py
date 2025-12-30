"""Must Reads functionality for acitrack.

Retrieves and ranks the most important publications for quick review.
"""

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Keywords for early cancer detection relevance
PRIORITY_KEYWORDS = [
    "screening",
    "biomarker",
    "early detection",
    "ctdna",
    "cell-free dna",
    "methylation",
    "liquid biopsy",
    "diagnostic",
    "detection method",
    "sensitivity",
    "specificity",
]

# High-priority source names (case-insensitive)
PRIORITY_SOURCES = {
    "nature cancer": 100,
    "science": 90,
    "the lancet": 80,
    "bmj": 70,
    "biorxiv (all)": 60,
    "medrxiv (all)": 60,
}


@dataclass
class MustRead:
    """A must-read publication."""

    id: str
    title: str
    published_date: str
    source: str
    venue: str
    url: str
    why_it_matters: str
    key_findings: List[str]
    rank_score: float
    rank_reason: str


def _compute_rank_score(
    title: str,
    summary: str,
    raw_text: str,
    source: str,
    published_date: str,
) -> tuple[float, str]:
    """Compute a rank score for a publication.

    Args:
        title: Publication title
        summary: AI-generated summary
        raw_text: Raw publication text
        source: Source name
        published_date: Publication date (ISO8601)

    Returns:
        Tuple of (score, reason) where score is 0-1000 and reason explains the score
    """
    score = 0.0
    reasons = []

    # 1. Source priority (0-100 points)
    source_lower = source.lower()
    source_score = 0
    for priority_source, priority_score in PRIORITY_SOURCES.items():
        if priority_source in source_lower:
            source_score = priority_score
            reasons.append(f"high-priority source ({source})")
            break
    if source_score == 0:
        source_score = 10  # baseline for any source
    score += source_score

    # 2. Recency (0-200 points)
    try:
        pub_date = datetime.fromisoformat(published_date.replace("Z", "+00:00"))
        if pub_date.tzinfo is None:
            pub_date = pub_date.replace(tzinfo=None)
            now = datetime.now()
        else:
            now = datetime.now().astimezone()
        age_days = (now - pub_date).days
        if age_days < 7:
            recency_score = 200
            reasons.append("very recent (< 7 days)")
        elif age_days < 14:
            recency_score = 150
            reasons.append("recent (< 14 days)")
        elif age_days < 30:
            recency_score = 100
            reasons.append("recent (< 30 days)")
        else:
            recency_score = 50
            reasons.append("older publication")
        score += recency_score
    except (ValueError, AttributeError):
        score += 50  # default for unparseable dates

    # 3. Keyword relevance (0-300 points)
    combined_text = f"{title} {summary} {raw_text}".lower()
    keyword_hits = []
    for keyword in PRIORITY_KEYWORDS:
        if keyword in combined_text:
            keyword_hits.append(keyword)

    if len(keyword_hits) >= 3:
        keyword_score = 300
        reasons.append(f"high keyword relevance ({len(keyword_hits)} matches)")
    elif len(keyword_hits) >= 2:
        keyword_score = 200
        reasons.append(f"moderate keyword relevance ({len(keyword_hits)} matches)")
    elif len(keyword_hits) >= 1:
        keyword_score = 100
        reasons.append(f"keyword match: {keyword_hits[0]}")
    else:
        keyword_score = 0
    score += keyword_score

    reason = "; ".join(reasons) if reasons else "baseline scoring"
    return score, reason


def _extract_key_findings(summary: str) -> List[str]:
    """Extract key findings from summary.

    Args:
        summary: AI-generated summary

    Returns:
        List of 1-3 key findings
    """
    if not summary or summary == "No summary available.":
        return []

    # Simple extraction: look for bullet points or split by periods
    # This is a heuristic; can be improved
    findings = []

    # Check for bullet points
    bullets = re.findall(r"[•\-\*]\s*(.+)", summary)
    if bullets:
        findings = bullets[:3]
    else:
        # Split by periods and take first few sentences
        sentences = [s.strip() for s in summary.split(".") if s.strip()]
        findings = sentences[:3]

    return findings


def _generate_why_it_matters(
    title: str, summary: str, rank_reason: str
) -> str:
    """Generate a 1-2 line 'why it matters' statement.

    Args:
        title: Publication title
        summary: AI-generated summary
        rank_reason: Reason for high ranking

    Returns:
        Brief explanation of why this matters
    """
    # Simple heuristic: use first sentence of summary or title-based reason
    if summary and summary != "No summary available.":
        first_sentence = summary.split(".")[0].strip()
        if len(first_sentence) > 20:
            return first_sentence + "."

    return f"Flagged as must-read: {rank_reason}."


def get_must_reads_from_db(
    db_path: str = "data/db/acitrack.db",
    since_days: int = 7,
    limit: int = 10,
) -> dict:
    """Retrieve must-reads from SQLite database.

    Args:
        db_path: Path to SQLite database
        since_days: Number of days to look back
        limit: Maximum number of must-reads to return

    Returns:
        Dictionary with must_reads list and metadata
    """
    db_file = Path(db_path)
    if not db_file.exists():
        logger.warning("Database not found at %s, using fallback", db_path)
        return _fallback_must_reads(since_days, limit)

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Calculate cutoff date
        cutoff_date = (
            datetime.now() - timedelta(days=since_days)
        ).date().isoformat()

        # Query publications from the last N days
        cursor.execute(
            """
            SELECT id, title, published_date, source, venue, url, raw_text, summary
            FROM publications
            WHERE published_date >= ?
            ORDER BY published_date DESC
        """,
            (cutoff_date,),
        )

        rows = cursor.fetchall()
        total_candidates = len(rows)

        # Rank publications
        ranked_pubs = []
        for row in rows:
            score, reason = _compute_rank_score(
                title=row["title"] or "",
                summary=row["summary"] or "",
                raw_text=row["raw_text"] or "",
                source=row["source"] or "",
                published_date=row["published_date"] or "",
            )

            key_findings = _extract_key_findings(row["summary"] or "")
            why_it_matters = _generate_why_it_matters(
                row["title"] or "", row["summary"] or "", reason
            )

            must_read = MustRead(
                id=row["id"],
                title=row["title"] or "Untitled",
                published_date=row["published_date"] or "",
                source=row["source"] or "",
                venue=row["venue"] or "",
                url=row["url"] or "",
                why_it_matters=why_it_matters,
                key_findings=key_findings,
                rank_score=score,
                rank_reason=reason,
            )
            ranked_pubs.append(must_read)

        # Sort by score (descending) and take top N
        ranked_pubs.sort(key=lambda x: x.rank_score, reverse=True)
        top_must_reads = ranked_pubs[:limit]

        conn.close()

        return {
            "must_reads": [
                {
                    "id": mr.id,
                    "title": mr.title,
                    "published_date": mr.published_date,
                    "source": mr.source,
                    "venue": mr.venue,
                    "url": mr.url,
                    "why_it_matters": mr.why_it_matters,
                    "key_findings": mr.key_findings,
                    "rank_score": mr.rank_score,
                    "rank_reason": mr.rank_reason,
                }
                for mr in top_must_reads
            ],
            "generated_at": datetime.now().isoformat(),
            "window_days": since_days,
            "total_candidates": total_candidates,
        }

    except Exception as e:
        logger.error("Error retrieving must-reads from database: %s", e)
        return _fallback_must_reads(since_days, limit)


def _fallback_must_reads(since_days: int, limit: int) -> dict:
    """Fallback to latest run outputs when DB is not available.

    Args:
        since_days: Number of days to look back
        limit: Maximum number of must-reads to return

    Returns:
        Dictionary with must_reads list and metadata
    """
    try:
        # Read latest publications JSON
        latest_pubs_path = None
        raw_dir = Path("data/raw")
        if raw_dir.exists():
            # Find most recent publications file
            pub_files = sorted(
                raw_dir.glob("*_publications.json"), reverse=True
            )
            if pub_files:
                latest_pubs_path = pub_files[0]

        if not latest_pubs_path or not latest_pubs_path.exists():
            logger.warning("No publications file found for fallback")
            return _empty_must_reads(since_days)

        with open(latest_pubs_path, "r", encoding="utf-8") as f:
            publications = json.load(f)

        # Filter by date
        cutoff_date = (
            datetime.now() - timedelta(days=since_days)
        ).date().isoformat()

        filtered_pubs = []
        for pub in publications:
            pub_date = pub.get("date", "")
            if pub_date >= cutoff_date:
                filtered_pubs.append(pub)

        total_candidates = len(filtered_pubs)

        # Rank publications
        ranked_pubs = []
        for pub in filtered_pubs:
            score, reason = _compute_rank_score(
                title=pub.get("title", ""),
                summary=pub.get("summary", ""),
                raw_text=pub.get("raw_text", ""),
                source=pub.get("source", ""),
                published_date=pub.get("date", ""),
            )

            key_findings = _extract_key_findings(pub.get("summary", ""))
            why_it_matters = _generate_why_it_matters(
                pub.get("title", ""), pub.get("summary", ""), reason
            )

            must_read = MustRead(
                id=pub.get("id", ""),
                title=pub.get("title", "Untitled"),
                published_date=pub.get("date", ""),
                source=pub.get("source", ""),
                venue=pub.get("venue", ""),
                url=pub.get("url", ""),
                why_it_matters=why_it_matters,
                key_findings=key_findings,
                rank_score=score,
                rank_reason=reason,
            )
            ranked_pubs.append(must_read)

        # Sort by score and take top N
        ranked_pubs.sort(key=lambda x: x.rank_score, reverse=True)
        top_must_reads = ranked_pubs[:limit]

        return {
            "must_reads": [
                {
                    "id": mr.id,
                    "title": mr.title,
                    "published_date": mr.published_date,
                    "source": mr.source,
                    "venue": mr.venue,
                    "url": mr.url,
                    "why_it_matters": mr.why_it_matters,
                    "key_findings": mr.key_findings,
                    "rank_score": mr.rank_score,
                    "rank_reason": mr.rank_reason,
                }
                for mr in top_must_reads
            ],
            "generated_at": datetime.now().isoformat(),
            "window_days": since_days,
            "total_candidates": total_candidates,
        }

    except Exception as e:
        logger.error("Error in fallback must-reads: %s", e)
        return _empty_must_reads(since_days)


def _empty_must_reads(since_days: int) -> dict:
    """Return empty must-reads response.

    Args:
        since_days: Number of days to look back

    Returns:
        Empty must-reads structure
    """
    return {
        "must_reads": [],
        "generated_at": datetime.now().isoformat(),
        "window_days": since_days,
        "total_candidates": 0,
    }
