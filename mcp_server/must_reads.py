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


def _normalize_relevancy_scores(must_reads: List[dict]) -> None:
    """[DEPRECATED] Normalize score_total to 0-100 relevancy_score scale in-place.

    This function is deprecated in favor of LLM-based relevancy scoring.
    It remains for backwards compatibility with poc_v1 scoring.

    Args:
        must_reads: List of must-read dictionaries with score_total field
    """
    if not must_reads:
        return

    # Extract all score_total values
    scores = [mr.get("score_total", 0) for mr in must_reads]
    min_score = min(scores)
    max_score = max(scores)

    # Handle edge case: all scores are equal
    if max_score == min_score:
        for mr in must_reads:
            mr["relevancy_score"] = 50
    else:
        # Normalize to 0-100 scale
        for mr in must_reads:
            score = mr.get("score_total", 0)
            normalized = ((score - min_score) / (max_score - min_score)) * 100
            mr["relevancy_score"] = round(normalized)


def _score_relevancy_with_llm(must_reads: List[dict]) -> None:
    """Score relevancy using LLM for each must_read item in-place.

    Uses the llm_relevancy module to compute LLM-based relevancy scores.
    Respects caching: items with scoring_version="poc_v2" are not re-scored.

    Args:
        must_reads: List of must-read dictionaries
    """
    if not must_reads:
        return

    try:
        from mcp_server.llm_relevancy import score_relevancy

        for mr in must_reads:
            # Score item (will use cache if available)
            result = score_relevancy(mr)

            # Update item with LLM scoring results
            mr["relevancy_score"] = result["relevancy_score"]
            mr["relevancy_reason"] = result["relevancy_reason"]
            mr["confidence"] = result["confidence"]
            mr["signals"] = result.get("signals", {})
            mr["scored_at"] = result["scored_at"]
            mr["scoring_version"] = result["scoring_version"]
            mr["scoring_model"] = result["scoring_model"]

            # Log errors if present
            if "error" in result:
                logger.warning("LLM scoring error for item %s: %s",
                             mr.get("id", "unknown"), result["error"])

    except ImportError as e:
        logger.error("Failed to import llm_relevancy module: %s", e)
        # Fallback to deprecated heuristic scoring
        logger.warning("Falling back to deprecated heuristic scoring (poc_v1)")
        _normalize_relevancy_scores(must_reads)


def _score_credibility_with_llm(must_reads: List[dict]) -> None:
    """Score credibility using LLM + citation signals for each must_read item in-place.

    Uses the llm_credibility module to compute credibility scores.
    Respects caching: items with scoring_version="poc_v3" are not re-scored.
    Updates scoring_version to "poc_v3" after credibility scoring.

    Args:
        must_reads: List of must-read dictionaries
    """
    if not must_reads:
        return

    try:
        from mcp_server.llm_credibility import score_credibility

        for mr in must_reads:
            # Score credibility (will use cache if available)
            result = score_credibility(mr)

            # Update item with credibility scoring results
            mr["credibility_score"] = result["credibility_score"]
            mr["credibility_reason"] = result["credibility_reason"]
            mr["credibility_confidence"] = result.get("credibility_confidence", "low")
            mr["credibility_signals"] = result.get("credibility_signals", {})

            # Update scoring_version to poc_v3 (indicates credibility scoring complete)
            mr["scoring_version"] = result["scoring_version"]

            # Log errors if present
            if "error" in result:
                logger.warning("Credibility scoring error for item %s: %s",
                             mr.get("id", "unknown"), result["error"])

    except ImportError as e:
        logger.error("Failed to import llm_credibility module: %s", e)
        logger.warning("Credibility scoring unavailable, leaving fields as placeholders")
        # Keep credibility fields as placeholders (None/"")
        for mr in must_reads:
            if "credibility_score" not in mr:
                mr["credibility_score"] = None
            if "credibility_reason" not in mr:
                mr["credibility_reason"] = ""


def get_must_reads_from_db(
    db_path: str = "data/db/acitrack.db",
    since_days: int = 7,
    limit: int = 10,
    use_ai: bool = True,
    rerank_max_candidates: int = 25,  # Reduced from 50 to ensure reliable parsing
) -> dict:
    """Retrieve must-reads from SQLite database with optional AI reranking.

    Args:
        db_path: Path to SQLite database
        since_days: Number of days to look back
        limit: Maximum number of must-reads to return
        use_ai: Whether to use AI reranking (default: True, requires OPENAI_API_KEY)
        rerank_max_candidates: Max candidates to pass to AI reranker (default: 25)

    Returns:
        Dictionary with must_reads list and metadata
    """
    db_file = Path(db_path)
    if not db_file.exists():
        logger.warning("Database not found at %s, using fallback", db_path)
        return _fallback_must_reads(since_days, limit, use_ai, rerank_max_candidates)

    try:
        # Import rerank modules
        from mcp_server.ai_reranker import rerank_with_openai, merge_rerank_results
        from mcp_server.rerank_cache import get_cached_rerank, store_rerank_results, RERANK_VERSION

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
        total_raw_pubs = len(rows)

        # Track filtering stats
        filter_stats = {
            "total_raw": total_raw_pubs,
            "missing_date": 0,
            "missing_url": 0,
            "missing_title": 0,
            "accepted": 0,
        }

        # STEP 1: Apply heuristic ranking to all candidates
        ranked_pubs = []
        for row in rows:
            # Filter out publications with missing critical fields
            if not row["published_date"]:
                filter_stats["missing_date"] += 1
                continue
            if not row["url"]:
                filter_stats["missing_url"] += 1
                continue
            if not row["title"]:
                filter_stats["missing_title"] += 1
                continue

            filter_stats["accepted"] += 1

            score, reason = _compute_rank_score(
                title=row["title"] or "",
                summary=row["summary"] or "",
                raw_text=row["raw_text"] or "",
                source=row["source"] or "",
                published_date=row["published_date"] or "",
            )

            ranked_pubs.append({
                "id": row["id"],
                "title": row["title"] or "Untitled",
                "published_date": row["published_date"] or "",
                "source": row["source"] or "",
                "venue": row["venue"] or "",
                "url": row["url"] or "",
                "raw_text": row["raw_text"] or "",
                "summary": row["summary"] or "",
                "heuristic_score": score,
                "heuristic_reason": reason,
            })

        # Log filtering summary
        total_candidates = len(ranked_pubs)
        if filter_stats["total_raw"] != filter_stats["accepted"]:
            dropped_total = filter_stats["total_raw"] - filter_stats["accepted"]
            logger.info(
                "Candidate filtering: %d raw → %d accepted (dropped %d: %d missing date, %d missing URL, %d missing title)",
                filter_stats["total_raw"],
                filter_stats["accepted"],
                dropped_total,
                filter_stats["missing_date"],
                filter_stats["missing_url"],
                filter_stats["missing_title"],
            )
        else:
            logger.info(
                "Candidate filtering: all %d publications have required fields",
                total_candidates,
            )

        # Sort by heuristic score and take top N candidates for reranking
        ranked_pubs.sort(key=lambda x: x["heuristic_score"], reverse=True)
        shortlist = ranked_pubs[:rerank_max_candidates]

        if len(ranked_pubs) > rerank_max_candidates:
            logger.info(
                "Shortlisting for AI rerank: top %d of %d candidates (based on heuristic scores)",
                len(shortlist),
                len(ranked_pubs),
            )

        # STEP 2: AI reranking (optional, with caching)
        used_ai = False
        if use_ai and shortlist:
            # Check cache first
            shortlist_ids = [p["id"] for p in shortlist]
            cached_results = get_cached_rerank(shortlist_ids, RERANK_VERSION, db_path)

            # Separate cached and uncached
            uncached_pubs = [p for p in shortlist if p["id"] not in cached_results]

            # Rerank uncached publications
            rerank_results = []
            if uncached_pubs:
                logger.info("Calling AI reranker for %d publications", len(uncached_pubs))
                rerank_results = rerank_with_openai(uncached_pubs)

            # If reranking succeeded, merge results and cache
            if rerank_results is not None:
                used_ai = True

                # Merge cached + new results
                all_rerank_results = list(rerank_results) if rerank_results else []

                # Build shortlist lookup to get title for cached items
                shortlist_by_id = {p["id"]: p for p in shortlist}

                for pub_id, cached_data in cached_results.items():
                    # Get title from shortlist for validation
                    title = shortlist_by_id.get(pub_id, {}).get("title", "")
                    all_rerank_results.append({
                        "pub_id": pub_id,  # Use pub_id for validation
                        "id": pub_id,  # Keep id for backward compatibility
                        "title": title,  # Add title for validation
                        "llm_score": cached_data["llm_score"],
                        "llm_rank": cached_data["llm_rank"],
                        "llm_reason": cached_data["llm_reason"],
                        "llm_why": cached_data["llm_why"],
                        "llm_findings": cached_data["llm_findings"],
                    })

                # Merge rerank data into shortlist (with validation)
                shortlist, validated_items = merge_rerank_results(shortlist, all_rerank_results)

                # Store ONLY validated new results in cache
                validated_new_items = [
                    item for item in validated_items
                    if item.get("pub_id") not in cached_results and item.get("pub_id") or item.get("id") not in cached_results
                ]
                if validated_new_items:
                    store_rerank_results(validated_new_items, model="gpt-4o-mini", rerank_version=RERANK_VERSION, db_path=db_path)

                # Sort by LLM rank (lower is better)
                shortlist.sort(key=lambda x: x.get("llm_rank", 999))
            else:
                logger.info("AI reranking not available, using heuristic scores")

        # STEP 3: Take top N results
        top_results = shortlist[:limit]

        conn.close()

        # STEP 4: Format output with scoring blend
        must_reads_output = []
        for pub in top_results:
            # Scoring blend: heuristic + scaled LLM score
            heuristic_score = pub.get("heuristic_score", 0)
            llm_score = pub.get("llm_score", 0)

            # Blend logic:
            # - If LLM says irrelevant (score < 10), strongly demote regardless of heuristic
            # - Otherwise, combine scores with LLM weighted more heavily
            if used_ai and llm_score > 0:
                if llm_score < 10:
                    # LLM says irrelevant - strongly demote
                    total_score = llm_score
                else:
                    # Blend: 40% heuristic + 60% LLM (scaled to match heuristic range)
                    # LLM score is 0-100, scale to match heuristic 0-600 range
                    llm_score_scaled = (llm_score / 100.0) * 600
                    total_score = (0.4 * heuristic_score) + (0.6 * llm_score_scaled)
            else:
                total_score = heuristic_score

            # Determine explanation and fields
            if used_ai and llm_score > 0:
                why_it_matters = pub.get("llm_why", "") or _generate_why_it_matters(
                    pub.get("title", ""), pub.get("summary", ""), pub.get("heuristic_reason", "")
                )
                key_findings = pub.get("llm_findings", []) or _extract_key_findings(pub.get("summary", ""))
                explanation = pub.get("llm_reason", "")
                tags = pub.get("llm_tags", [])
                confidence = pub.get("llm_confidence", "medium")
            else:
                why_it_matters = _generate_why_it_matters(
                    pub.get("title", ""), pub.get("summary", ""), pub.get("heuristic_reason", "")
                )
                key_findings = _extract_key_findings(pub.get("summary", ""))
                explanation = pub.get("heuristic_reason", "")
                tags = []
                confidence = None

            must_reads_output.append({
                "id": pub.get("id", ""),
                "title": pub.get("title", ""),
                "published_date": pub.get("published_date", ""),
                "source": pub.get("source", ""),
                "venue": pub.get("venue", ""),
                "url": pub.get("url", ""),
                "score_total": total_score,
                "score_components": {
                    "heuristic": heuristic_score,
                    "llm": llm_score if used_ai else None,
                },
                "explanation": explanation,
                "why_it_matters": why_it_matters,
                "key_findings": key_findings,
                "tags": tags,
                "confidence": confidence,
            })

        # Score relevancy using LLM (replaces old heuristic normalization)
        _score_relevancy_with_llm(must_reads_output)

        # Score credibility using LLM + citation signals (after relevancy)
        _score_credibility_with_llm(must_reads_output)

        return {
            "must_reads": must_reads_output,
            "generated_at": datetime.now().isoformat(),
            "window_days": since_days,
            "total_candidates": total_candidates,
            "used_ai": used_ai,
            "rerank_version": RERANK_VERSION if used_ai else None,
        }

    except Exception as e:
        logger.error("Error retrieving must-reads from database: %s", e)
        return _fallback_must_reads(since_days, limit, use_ai, rerank_max_candidates)


def _fallback_must_reads(since_days: int, limit: int, use_ai: bool = True, rerank_max_candidates: int = 25) -> dict:
    """Fallback to latest run outputs when DB is not available.

    Args:
        since_days: Number of days to look back
        limit: Maximum number of must-reads to return
        use_ai: Whether to use AI reranking (ignored in fallback for simplicity)
        rerank_max_candidates: Max candidates for reranking (ignored in fallback)

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

        must_reads_list = [
            {
                "id": mr.id,
                "title": mr.title,
                "published_date": mr.published_date,
                "source": mr.source,
                "venue": mr.venue,
                "url": mr.url,
                "score_total": mr.rank_score,
                "score_components": {
                    "heuristic": mr.rank_score,
                    "llm": None,
                },
                "explanation": mr.rank_reason,
                "why_it_matters": mr.why_it_matters,
                "key_findings": mr.key_findings,
                "tags": [],
                "confidence": None,
            }
            for mr in top_must_reads
        ]

        # Score relevancy using LLM (replaces old heuristic normalization)
        _score_relevancy_with_llm(must_reads_list)

        # Score credibility using LLM + citation signals (after relevancy)
        _score_credibility_with_llm(must_reads_list)

        return {
            "must_reads": must_reads_list,
            "generated_at": datetime.now().isoformat(),
            "window_days": since_days,
            "total_candidates": total_candidates,
            "used_ai": False,
            "rerank_version": None,
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
        "used_ai": False,
        "rerank_version": None,
    }
