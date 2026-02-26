"""Data access layer for weekly digest queries.

Supports both SQLite and PostgreSQL backends.
"""

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, date
from typing import Dict, List, Optional, Any, Set, Tuple

logger = logging.getLogger(__name__)

# Score ordinal mappings for display
SCORE_ORDINALS = {
    "high": {"min": 80, "label": "High", "description": "Strong signal"},
    "moderate": {"min": 65, "label": "Moderate", "description": "Notable signal"},
    "exploratory": {"min": 0, "label": "Exploratory", "description": "Worth monitoring"},
}


def score_to_ordinal(score: Optional[float]) -> str:
    """Convert numeric score to ordinal label."""
    if score is None:
        return "Exploratory"
    if score >= SCORE_ORDINALS["high"]["min"]:
        return "High"
    elif score >= SCORE_ORDINALS["moderate"]["min"]:
        return "Moderate"
    return "Exploratory"


def _clean_why_it_matters(text: str) -> str:
    """Clean 'why it matters' text to remove reviewer attribution and be concise.

    Removes phrases like:
    - "Claude's review found that..."
    - "Both reviews agree that..."
    - "Gemini's analysis indicates..."
    - "According to the reviewers..."
    """
    if not text:
        return ""

    # Patterns to remove reviewer attribution
    attribution_patterns = [
        r"^(Claude'?s?|Gemini'?s?|GPT'?s?|Both|All|The)\s+(review|reviews|analysis|analyses|evaluation|evaluations)\s+(found|agree|agrees|indicate|indicates|suggest|suggests|show|shows|note|notes|highlight|highlights)\s+(that\s+)?",
        r"^According to (the )?(Claude'?s?|Gemini'?s?|GPT'?s?|reviewers?|analysis|analyses),?\s*",
        r"^(Both|All) (reviewers?|models?|analyses) (agree|note|highlight|found|suggest)\s+(that\s+)?",
        r"^The (Claude|Gemini|GPT|AI|model) (review|analysis) (found|indicates|suggests|notes)\s+(that\s+)?",
    ]

    cleaned = text.strip()
    for pattern in attribution_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

    # Capitalize first letter if needed
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]

    # Truncate to ~2 sentences if too long
    sentences = re.split(r'(?<=[.!?])\s+', cleaned)
    if len(sentences) > 2:
        cleaned = ' '.join(sentences[:2])
        if not cleaned.endswith('.'):
            cleaned += '.'

    return cleaned.strip()


def _generate_fallback_why_it_matters(pub: Dict) -> str:
    """Generate fallback 'why it matters' when none is available."""
    title = pub.get("title", "")
    source = pub.get("source", "")
    venue = pub.get("venue", "")

    # Try to infer topic from title
    cancer_types = ["lung", "breast", "colorectal", "pancreatic", "prostate", "ovarian", "liver"]
    detection_terms = ["screening", "early detection", "biomarker", "liquid biopsy", "ctDNA", "diagnostic"]

    cancer_match = None
    detection_match = None

    title_lower = title.lower()
    for cancer in cancer_types:
        if cancer in title_lower:
            cancer_match = cancer.capitalize()
            break

    for term in detection_terms:
        if term in title_lower:
            detection_match = term
            break

    if cancer_match and detection_match:
        return f"This study addresses {cancer_match.lower()} cancer {detection_match}, a key area for early detection research."
    elif cancer_match:
        return f"This research focuses on {cancer_match.lower()} cancer, contributing to the early detection knowledge base."
    elif detection_match:
        return f"This study explores {detection_match} methods relevant to cancer early detection."
    elif venue:
        return f"Published in {venue}, this study may offer insights relevant to cancer early detection research."
    elif source:
        return f"From {source}, this publication may contain findings relevant to early detection efforts."

    return "This publication was identified as potentially relevant to cancer early detection research."


def _parse_credibility_signals(pub: Dict, must_read: Dict) -> Dict:
    """Parse credibility signals, preferring centralized data from pub."""
    # Try centralized column first
    signals = pub.get("credibility_signals_json")
    if signals:
        if isinstance(signals, str):
            try:
                return json.loads(signals)
            except (json.JSONDecodeError, TypeError):
                return {}
        return signals if isinstance(signals, dict) else {}

    # Fall back to must_read enrichment
    return must_read.get("credibility_signals", {})


def get_database_url() -> Optional[str]:
    """Get database URL from environment."""
    return os.environ.get("DATABASE_URL")


def is_postgres() -> bool:
    """Check if using PostgreSQL backend."""
    db_url = get_database_url()
    return db_url is not None and db_url.startswith("postgresql://")


def _get_sqlite_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Get SQLite connection."""
    if db_path is None:
        db_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "db", "acitrack.db"
        )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _get_postgres_connection(database_url: Optional[str] = None):
    """Get PostgreSQL connection."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    if database_url is None:
        database_url = get_database_url()

    conn = psycopg2.connect(database_url)
    return conn


def _get_available_columns(conn, table_name: str, is_pg: bool) -> Set[str]:
    """Get list of available columns in a table."""
    cursor = conn.cursor()

    if is_pg:
        cursor.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s
        """, (table_name,))
        columns = {row[0] for row in cursor.fetchall()}
    else:
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = {row[1] for row in cursor.fetchall()}

    cursor.close()
    return columns


def _build_link(pub: Dict) -> Optional[str]:
    """Build link for a publication using fallback chain.

    Priority:
    1. canonical_url if present and non-empty
    2. url if present and non-empty
    3. doi -> https://doi.org/<doi>
    4. pmid -> https://pubmed.ncbi.nlm.nih.gov/<pmid>/
    5. None
    """
    if pub.get("canonical_url"):
        return pub["canonical_url"]

    if pub.get("url"):
        return pub["url"]

    if pub.get("doi"):
        return f"https://doi.org/{pub['doi']}"

    if pub.get("pmid"):
        return f"https://pubmed.ncbi.nlm.nih.gov/{pub['pmid']}/"

    return None


def get_publications_for_week(
    week_start: date,
    week_end: date,
    top_n: int = 5,
    honorable_mentions: int = 0,
    db_path: Optional[str] = None,
    database_url: Optional[str] = None,
    debug_ranking: bool = False,
    min_relevancy_score: Optional[float] = None,
) -> Dict[str, Any]:
    """Get top publications for a week.

    Args:
        week_start: Start of week (inclusive)
        week_end: End of week (inclusive)
        top_n: Number of top publications to return
        honorable_mentions: Number of honorable mentions to include
        db_path: SQLite database path (optional)
        database_url: PostgreSQL connection URL (optional)
        debug_ranking: If True, include debug data (top 20 candidates, ranking diagnostics)
        min_relevancy_score: Minimum relevancy score threshold. Publications below
            this score are excluded from must_reads and honorable_mentions.

    Returns:
        Dictionary with:
        - must_reads: List of top N publications
        - honorable_mentions: List of honorable mention publications
        - total_candidates: Total publications in date range
        - scoring_method: Description of scoring used
        - debug_ranking: (if debug_ranking=True) Top 20 candidates with full score breakdown
        - ranking_warnings: (if debug_ranking=True) Any warnings about score anomalies
    """
    use_pg = database_url or is_postgres()

    if use_pg:
        return _get_publications_postgres(
            week_start, week_end, top_n, honorable_mentions, database_url,
            debug_ranking, min_relevancy_score
        )
    else:
        return _get_publications_sqlite(
            week_start, week_end, top_n, honorable_mentions, db_path,
            debug_ranking, min_relevancy_score
        )


def _get_publications_postgres(
    week_start: date,
    week_end: date,
    top_n: int,
    honorable_mentions: int,
    database_url: Optional[str],
    debug_ranking: bool = False,
    min_relevancy_score: Optional[float] = None,
) -> Dict[str, Any]:
    """Get publications from PostgreSQL."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    conn = _get_postgres_connection(database_url)

    try:
        # Get available columns
        pub_columns = _get_available_columns(conn, "publications", True)

        # Build SELECT clause based on available columns
        select_cols = ["publication_id as id", "title", "published_date", "source"]

        # Optional columns with safe fallbacks
        optional_cols = [
            "canonical_url", "url", "doi", "pmid", "venue", "authors",
            "summary", "raw_text", "source_type",
            # Centralized scoring columns (from migration 003)
            "final_relevancy_score", "final_relevancy_reason", "final_summary",
            "agreement_level", "confidence",
            "credibility_score", "credibility_reason", "credibility_confidence",
            "credibility_signals_json", "claude_score", "gemini_score",
            "evaluator_rationale", "disagreements", "final_signals_json",
            "scoring_run_id",
            # Legacy columns (older schemas without centralized scoring)
            "latest_relevancy_score as relevancy_score_legacy",
            "latest_credibility_score as credibility_score_legacy",
        ]

        for col in optional_cols:
            # Handle aliased columns
            base_col = col.split(" as ")[0].strip()
            if base_col in pub_columns:
                select_cols.append(col)

        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Query publications in date range
        query = f"""
            SELECT {', '.join(select_cols)}
            FROM publications
            WHERE published_date >= %s AND published_date < %s + INTERVAL '1 day'
            ORDER BY published_date DESC
        """

        cursor.execute(query, (week_start, week_end))
        rows = cursor.fetchall()

        # Check if centralized scoring columns are available
        has_centralized_scoring = "final_relevancy_score" in pub_columns

        # Only fall back to tri_model/must_reads enrichment if centralized
        # scoring columns are not yet populated on publications
        if has_centralized_scoring:
            must_reads_data = {}
            tri_model_data = {}
            logger.info("Using centralized scoring from publications table")
        else:
            # Legacy path: enrich from separate tables
            must_reads_data = _get_must_reads_for_period_postgres(
                conn, week_start, week_end
            )
            tri_model_data = _get_tri_model_data_postgres(
                conn, week_start, week_end
            )
            logger.info("Using legacy enrichment from tri_model_events + must_reads")

        cursor.close()

        return _process_publications(
            list(rows), week_start, week_end, top_n, honorable_mentions,
            must_reads_data, tri_model_data, debug_ranking,
            min_relevancy_score
        )

    finally:
        conn.close()


def _get_must_reads_for_period_postgres(
    conn,
    week_start: date,
    week_end: date,
) -> Dict[str, Dict]:
    """Get must_reads data for publications in the period."""
    from psycopg2.extras import RealDictCursor

    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # Get the most recent must_reads that covers our period
        cursor.execute("""
            SELECT must_reads_json, created_at
            FROM must_reads
            WHERE created_at >= %s AND created_at < %s + INTERVAL '8 days'
            ORDER BY created_at DESC
            LIMIT 5
        """, (week_start, week_end))

        result = {}
        for row in cursor.fetchall():
            if row["must_reads_json"]:
                data = json.loads(row["must_reads_json"])
                for item in data.get("must_reads", []):
                    pub_id = item.get("id")
                    if pub_id and pub_id not in result:
                        result[pub_id] = item

        return result

    except Exception as e:
        logger.warning("Failed to get must_reads data: %s", e)
        return {}
    finally:
        cursor.close()


def _get_tri_model_data_postgres(
    conn,
    week_start: date,
    week_end: date,
) -> Dict[str, Dict]:
    """Get tri_model_events data for publications in the period (schema-tolerant).

    Dynamically detects available columns to handle schema differences.
    """
    from psycopg2.extras import RealDictCursor

    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # First, detect available columns in tri_model_events
        cursor.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'tri_model_events'
        """)
        available_columns = {row["column_name"] for row in cursor.fetchall()}

        if not available_columns:
            logger.warning("tri_model_events table not found or has no columns")
            return {}

        # Define desired columns (in priority order)
        desired_columns = [
            "publication_id", "title", "final_relevancy_score",
            "final_relevancy_reason", "final_summary", "confidence",
            "gpt_eval_json", "claude_review_json", "gemini_review_json",
            "agreement_level", "disagreements", "evaluator_rationale",
        ]

        # Filter to only columns that exist
        select_columns = [col for col in desired_columns if col in available_columns]

        # Must have at least publication_id and final_relevancy_score
        if "publication_id" not in select_columns:
            logger.warning("tri_model_events missing publication_id column")
            return {}

        if "final_relevancy_score" not in select_columns:
            logger.warning("tri_model_events missing final_relevancy_score column")
            # Still continue - we might get other useful data

        col_list = ", ".join(select_columns)
        logger.debug("tri_model_events query columns: %s", col_list)

        cursor.execute(f"""
            SELECT {col_list}
            FROM tri_model_events
            WHERE created_at >= %s AND created_at < %s + INTERVAL '8 days'
            ORDER BY created_at DESC
        """, (week_start, week_end))

        result = {}
        for row in cursor.fetchall():
            pub_id = row["publication_id"]
            if pub_id not in result:
                item = dict(row)
                # Parse JSON fields
                for json_field in ["gpt_eval_json", "claude_review_json", "gemini_review_json"]:
                    if item.get(json_field):
                        try:
                            item[json_field] = json.loads(item[json_field])
                        except (json.JSONDecodeError, TypeError):
                            pass
                result[pub_id] = item

        logger.info("Retrieved %d tri_model_events records", len(result))
        return result

    except Exception as e:
        logger.warning("Failed to get tri_model data: %s", e)
        return {}
    finally:
        cursor.close()


def _get_publications_sqlite(
    week_start: date,
    week_end: date,
    top_n: int,
    honorable_mentions: int,
    db_path: Optional[str],
    debug_ranking: bool = False,
    min_relevancy_score: Optional[float] = None,
) -> Dict[str, Any]:
    """Get publications from SQLite."""
    conn = _get_sqlite_connection(db_path)

    try:
        # Get available columns
        pub_columns = _get_available_columns(conn, "publications", False)

        # Build SELECT clause based on available columns
        select_cols = ["id", "title", "published_date", "source"]

        # Optional columns
        optional_cols = [
            "canonical_url", "url", "doi", "pmid", "venue", "authors",
            "summary", "raw_text", "source_type",
            # Centralized scoring columns (from migration v9)
            "final_relevancy_score", "final_relevancy_reason", "final_summary",
            "agreement_level", "confidence",
            "credibility_score", "credibility_reason", "credibility_confidence",
            "credibility_signals_json", "claude_score", "gemini_score",
            "evaluator_rationale", "disagreements", "final_signals_json",
            "scoring_run_id",
            # Legacy columns
            "relevance_score as relevancy_score_legacy",
        ]

        for col in optional_cols:
            base_col = col.split(" as ")[0].strip()
            if base_col in pub_columns:
                select_cols.append(col)

        cursor = conn.cursor()

        # Query publications in date range
        query = f"""
            SELECT {', '.join(select_cols)}
            FROM publications
            WHERE date(published_date) >= date(?) AND date(published_date) <= date(?)
            ORDER BY published_date DESC
        """

        cursor.execute(query, (week_start.isoformat(), week_end.isoformat()))
        rows = [dict(row) for row in cursor.fetchall()]

        cursor.close()

        return _process_publications(
            rows, week_start, week_end, top_n, honorable_mentions,
            {}, {},  # No legacy enrichment needed for SQLite
            debug_ranking, min_relevancy_score
        )

    finally:
        conn.close()


def _process_publications(
    publications: List[Dict],
    week_start: date,
    week_end: date,
    top_n: int,
    honorable_mentions: int,
    must_reads_data: Dict[str, Dict],
    tri_model_data: Dict[str, Dict],
    debug_ranking: bool = False,
    min_relevancy_score: Optional[float] = None,
) -> Dict[str, Any]:
    """Process and rank publications.

    Scoring priority (strict ordering):
    1. relevancy_score DESC (only ranking key)
    2. Publication date DESC (tie-breaker only)
    3. Title ASC (deterministic)

    If min_relevancy_score is set, only publications with a relevancy_score
    at or above the threshold are included in must_reads and honorable_mentions.
    """
    scored_pubs = []
    ranking_warnings = []

    for pub in publications:
        pub_id = pub.get("id")

        # Enrich from must_reads / tri_model data (legacy fallback)
        must_read = must_reads_data.get(pub_id, {})
        tri_model = tri_model_data.get(pub_id, {})

        # --- Scoring: prefer centralized columns on publications row ---
        # If the publications table has final_relevancy_score populated,
        # use it directly. Otherwise fall back to legacy enrichment.
        relevancy = pub.get("final_relevancy_score")

        if relevancy is None:
            # Legacy fallback: try tri_model_events or must_reads enrichment
            gpt_eval = tri_model.get("gpt_eval_json") or {}
            if isinstance(gpt_eval, str):
                try:
                    gpt_eval = json.loads(gpt_eval)
                except (json.JSONDecodeError, TypeError):
                    gpt_eval = {}

            relevancy = next(
                (s for s in [
                    tri_model.get("final_relevancy_score"),
                    gpt_eval.get("final_relevancy_score"),
                    must_read.get("final_relevancy_score"),
                    pub.get("relevancy_score_legacy"),
                    pub.get("relevancy_score"),
                ] if s is not None),
                None,
            )
        else:
            gpt_eval = {}

        credibility = pub.get("credibility_score") or must_read.get("credibility_score")

        # Individual reviewer scores: prefer centralized, fall back to JSON parsing
        claude_score = pub.get("claude_score")
        gemini_score = pub.get("gemini_score")

        if claude_score is None and tri_model:
            claude_review = tri_model.get("claude_review_json") or {}
            if isinstance(claude_review, str):
                try:
                    claude_review = json.loads(claude_review)
                except (json.JSONDecodeError, TypeError):
                    claude_review = {}
            if claude_review.get("review"):
                claude_score = claude_review.get("review", {}).get("relevancy_score")
            elif claude_review:
                claude_score = claude_review.get("relevancy_score")

        if gemini_score is None and tri_model:
            gemini_review = tri_model.get("gemini_review_json") or {}
            if isinstance(gemini_review, str):
                try:
                    gemini_review = json.loads(gemini_review)
                except (json.JSONDecodeError, TypeError):
                    gemini_review = {}
            if gemini_review.get("review"):
                gemini_score = gemini_review.get("review", {}).get("relevancy_score")
            elif gemini_review:
                gemini_score = gemini_review.get("relevancy_score")

        # Why it matters: prefer centralized, fall back to legacy
        raw_why_it_matters = (
            pub.get("final_relevancy_reason") or
            must_read.get("final_relevancy_reason") or
            tri_model.get("final_relevancy_reason") or
            gpt_eval.get("final_relevancy_reason") or
            ""
        )
        cleaned_why = _clean_why_it_matters(raw_why_it_matters)

        if not cleaned_why:
            cleaned_why = _generate_fallback_why_it_matters(pub)

        # Summary: prefer centralized, fall back to legacy
        summary_text = (
            pub.get("final_summary") or
            must_read.get("final_summary") or
            tri_model.get("final_summary") or
            gpt_eval.get("final_summary") or
            pub.get("summary") or
            (pub.get("raw_text", "")[:500] if pub.get("raw_text") else "")
        )

        # Confidence and agreement: prefer centralized
        confidence = (
            pub.get("confidence") or
            must_read.get("confidence") or
            tri_model.get("confidence") or
            gpt_eval.get("confidence") or
            must_read.get("credibility_confidence", "")
        )

        agreement_level = (
            pub.get("agreement_level") or
            must_read.get("agreement_level") or
            tri_model.get("agreement_level") or
            gpt_eval.get("agreement_level", "")
        )

        # Evaluator rationale and disagreements: prefer centralized
        evaluator_rationale = (
            pub.get("evaluator_rationale") or
            tri_model.get("evaluator_rationale") or
            gpt_eval.get("evaluator_rationale", "")
        )

        disagreements = pub.get("disagreements") or gpt_eval.get("disagreements", [])
        if isinstance(disagreements, str):
            try:
                disagreements = json.loads(disagreements)
            except:
                disagreements = [disagreements] if disagreements else []

        # Final signals: prefer centralized
        final_signals_raw = pub.get("final_signals_json") or gpt_eval.get("final_signals", {})
        if isinstance(final_signals_raw, str):
            try:
                final_signals = json.loads(final_signals_raw)
            except (json.JSONDecodeError, TypeError):
                final_signals = {}
        else:
            final_signals = final_signals_raw if final_signals_raw else {}

        # Build enriched publication
        enriched = {
            "id": pub_id,
            "title": pub.get("title", "") or "Untitled Publication",
            "published_date": pub.get("published_date"),
            "source": pub.get("source", "") or "Unknown Source",
            "venue": pub.get("venue") or pub.get("source", "") or "Unknown Venue",
            "authors": pub.get("authors", ""),
            "link": _build_link(pub),
            "canonical_url": pub.get("canonical_url"),
            "url": pub.get("url"),
            "doi": pub.get("doi"),
            "pmid": pub.get("pmid"),
            "relevancy_score": relevancy,
            "credibility_score": credibility,
            # Individual reviewer scores
            "claude_score": claude_score,
            "gemini_score": gemini_score,
            # Ordinal labels for display
            "relevancy_ordinal": score_to_ordinal(relevancy),
            "credibility_ordinal": score_to_ordinal(credibility),
            # Enrichment from must_reads, tri_model, or gpt_eval
            "summary": summary_text,
            "why_it_matters": cleaned_why,
            "key_findings": _extract_key_findings(must_read, tri_model, pub),
            "commercial_signals": _parse_credibility_signals(pub, must_read),
            "confidence": confidence,
            "agreement_level": agreement_level,
            "credibility_reason": pub.get("credibility_reason") or must_read.get("credibility_reason", ""),
            # Additional tri-model details
            "evaluator_rationale": evaluator_rationale,
            "disagreements": disagreements,
            "final_signals": final_signals,
        }

        scored_pubs.append(enriched)

    # Sort with STRICT ordering: relevancy desc, publication_date desc, title asc
    scored_pubs.sort(
        key=lambda x: (
            -(x["relevancy_score"] if x["relevancy_score"] is not None else -1),  # Primary: relevancy DESC
            -_date_to_ordinal(x["published_date"]),                               # Secondary: date DESC
            (x["title"] or "").lower(),                                           # Tertiary: title ASC
        )
    )

    # Apply minimum relevancy score threshold if set.
    # Filter AFTER sorting so that total_candidates still reflects all scored
    # publications, but only those meeting the threshold are eligible for selection.
    if min_relevancy_score is not None:
        eligible_pubs = [
            p for p in scored_pubs
            if p.get("relevancy_score") is not None
            and p["relevancy_score"] >= min_relevancy_score
        ]
        logger.info(
            "Score threshold %.1f applied: %d of %d scored publications eligible",
            min_relevancy_score, len(eligible_pubs), len(scored_pubs),
        )
    else:
        eligible_pubs = scored_pubs

    # Validate ranking: check for anomalies
    for i, pub in enumerate(eligible_pubs[:top_n]):
        score = pub.get("relevancy_score")
        score_val = score if score is not None else -1

        # Check if any higher-scored items appear later
        for j, later_pub in enumerate(eligible_pubs[i + 1:top_n + 5], start=i + 1):
            later_score = later_pub.get("relevancy_score")
            later_val = later_score if later_score is not None else -1
            if later_val > score_val:
                warning = (
                    f"Ranking anomaly: #{i+1} '{pub['title'][:40]}...' "
                    f"(relevancy={score_val:.1f}) outranked #{j+1} '{later_pub['title'][:40]}...' "
                    f"(relevancy={later_val:.1f})"
                )
                ranking_warnings.append(warning)
                logger.warning(warning)

        # Warn if 80+ score items not in top 5
        if score_val >= 80 and i >= top_n:
            warning = (
                f"High-score exclusion: '{pub['title'][:40]}...' "
                f"(relevancy={score_val:.1f}) not in top {top_n}"
            )
            ranking_warnings.append(warning)
            logger.warning(warning)

    # Split into must_reads and honorable_mentions
    must_reads = eligible_pubs[:top_n]
    mentions = eligible_pubs[top_n:top_n + honorable_mentions] if honorable_mentions > 0 else []

    scoring_method = "relevancy_only"

    # Count only publications that were actually scored (have a relevancy_score)
    total_scored = sum(1 for p in scored_pubs if p.get("relevancy_score") is not None)

    result = {
        "must_reads": must_reads,
        "honorable_mentions": mentions,
        "total_candidates": total_scored,
        "scoring_method": scoring_method,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "min_relevancy_score": min_relevancy_score,
    }

    # Add debug data if requested
    if debug_ranking:
        top_20_debug = []
        for i, pub in enumerate(scored_pubs[:20]):
            top_20_debug.append({
                "rank": i + 1,
                "publication_id": pub["id"],
                "title": pub["title"][:60] + "..." if len(pub["title"]) > 60 else pub["title"],
                "relevancy_score": pub["relevancy_score"],
                "credibility_score": pub["credibility_score"],
                "publication_date": str(pub["published_date"])[:10] if pub["published_date"] else None,
            })

        total_with_relevancy = sum(1 for p in scored_pubs if p.get("relevancy_score") is not None)
        relevancy_scores = [
            (p.get("relevancy_score") if p.get("relevancy_score") is not None else 0)
            for p in scored_pubs
        ]

        result["debug_ranking"] = {
            "ranking_method": "relevancy_only",
            "top_20_candidates": top_20_debug,
            "ranking_warnings": ranking_warnings,
            "total_candidates": len(scored_pubs),
            "total_with_relevancy": total_with_relevancy,
            "relevancy_distribution": {
                "high_80_plus": sum(1 for s in relevancy_scores if s >= 80),
                "moderate_65_79": sum(1 for s in relevancy_scores if 65 <= s < 80),
                "exploratory_below_65": sum(1 for s in relevancy_scores if s < 65),
            },
        }

    return result


def _extract_key_findings(
    must_read: Dict,
    tri_model: Dict,
    pub: Dict,
) -> List[str]:
    """Extract key findings/takeaways for a publication."""
    # Priority: must_read key_findings > tri_model final_summary split > summary split

    # Check for stored findings
    if must_read.get("key_findings"):
        findings = must_read["key_findings"]
        if isinstance(findings, list):
            return findings[:3]
        elif isinstance(findings, str):
            return [findings]

    # Try to extract from summary
    summary = (
        must_read.get("final_summary") or
        pub.get("summary") or
        ""
    )

    if summary:
        # Split by bullet points or sentences
        if "•" in summary:
            bullets = [b.strip() for b in summary.split("•") if b.strip()]
            return bullets[:3]
        elif "\n-" in summary:
            bullets = [b.strip() for b in summary.split("\n-") if b.strip()]
            return bullets[:3]
        else:
            # Split by sentences, take first 3
            sentences = [s.strip() + "." for s in summary.split(".") if s.strip()]
            return sentences[:3]

    return []


def _date_to_ordinal(date_val: Any) -> int:
    """Convert date to ordinal for sorting."""
    if date_val is None:
        return 0

    if isinstance(date_val, datetime):
        return date_val.toordinal()
    elif isinstance(date_val, date):
        return date_val.toordinal()
    elif isinstance(date_val, str):
        try:
            dt = datetime.fromisoformat(date_val.replace("Z", "+00:00"))
            return dt.toordinal()
        except (ValueError, AttributeError):
            return 0

    return 0


def log_digest_send(
    week_start: date,
    week_end: date,
    top_n: int,
    honorable_mentions: int,
    recipients: List[str],
    selected_ids: List[str],
    output_dir: str,
    send_mode: str,
    send_status: str,
    error: Optional[str] = None,
    db_path: Optional[str] = None,
    database_url: Optional[str] = None,
) -> bool:
    """Log a digest send to the database.

    Returns True if logged successfully, False otherwise.
    """
    use_pg = database_url or is_postgres()

    try:
        if use_pg:
            return _log_digest_send_postgres(
                week_start, week_end, top_n, honorable_mentions,
                recipients, selected_ids, output_dir, send_mode, send_status, error,
                database_url
            )
        else:
            return _log_digest_send_sqlite(
                week_start, week_end, top_n, honorable_mentions,
                recipients, selected_ids, output_dir, send_mode, send_status, error,
                db_path
            )
    except Exception as e:
        logger.warning("Failed to log digest send: %s", e)
        return False


def _log_digest_send_postgres(
    week_start: date,
    week_end: date,
    top_n: int,
    honorable_mentions: int,
    recipients: List[str],
    selected_ids: List[str],
    output_dir: str,
    send_mode: str,
    send_status: str,
    error: Optional[str],
    database_url: Optional[str],
) -> bool:
    """Log digest send to PostgreSQL."""
    conn = _get_postgres_connection(database_url)

    try:
        cursor = conn.cursor()

        # Create table if not exists (idempotent)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS weekly_digest_sends (
                id SERIAL PRIMARY KEY,
                week_start DATE NOT NULL,
                week_end DATE NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                top_n INTEGER NOT NULL,
                honorable_mentions INTEGER NOT NULL,
                recipients_json TEXT,
                selected_ids_json TEXT,
                output_dir TEXT,
                send_mode TEXT NOT NULL,
                send_status TEXT NOT NULL,
                error TEXT
            )
        """)

        cursor.execute("""
            INSERT INTO weekly_digest_sends (
                week_start, week_end, top_n, honorable_mentions,
                recipients_json, selected_ids_json, output_dir,
                send_mode, send_status, error
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            week_start, week_end, top_n, honorable_mentions,
            json.dumps(recipients), json.dumps(selected_ids), output_dir,
            send_mode, send_status, error
        ))

        conn.commit()
        cursor.close()
        return True

    except Exception as e:
        logger.error("Failed to log to PostgreSQL: %s", e)
        conn.rollback()
        return False
    finally:
        conn.close()


def _log_digest_send_sqlite(
    week_start: date,
    week_end: date,
    top_n: int,
    honorable_mentions: int,
    recipients: List[str],
    selected_ids: List[str],
    output_dir: str,
    send_mode: str,
    send_status: str,
    error: Optional[str],
    db_path: Optional[str],
) -> bool:
    """Log digest send to SQLite."""
    conn = _get_sqlite_connection(db_path)

    try:
        cursor = conn.cursor()

        # Create table if not exists (idempotent)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS weekly_digest_sends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL,
                week_end TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                top_n INTEGER NOT NULL,
                honorable_mentions INTEGER NOT NULL,
                recipients_json TEXT,
                selected_ids_json TEXT,
                output_dir TEXT,
                send_mode TEXT NOT NULL,
                send_status TEXT NOT NULL,
                error TEXT
            )
        """)

        cursor.execute("""
            INSERT INTO weekly_digest_sends (
                week_start, week_end, top_n, honorable_mentions,
                recipients_json, selected_ids_json, output_dir,
                send_mode, send_status, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            week_start.isoformat(), week_end.isoformat(), top_n, honorable_mentions,
            json.dumps(recipients), json.dumps(selected_ids), output_dir,
            send_mode, send_status, error
        ))

        conn.commit()
        cursor.close()
        return True

    except Exception as e:
        logger.error("Failed to log to SQLite: %s", e)
        return False
    finally:
        conn.close()


def log_publication_feedback(
    week_start: str,
    week_end: str,
    publication_id: str,
    vote: str,
    source_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
    database_url: Optional[str] = None,
) -> bool:
    """Persist per-paper digest feedback vote.

    Args:
        week_start: ISO date string of digest week start (YYYY-MM-DD)
        week_end: ISO date string of digest week end (YYYY-MM-DD)
        publication_id: Publication identifier from digest
        vote: "up" or "down"
        source_ip: Optional request IP
        user_agent: Optional request user-agent
        context: Optional metadata dict (stored as JSON)
    """
    if vote not in {"up", "down"}:
        raise ValueError("vote must be 'up' or 'down'")

    use_pg = database_url or is_postgres()

    try:
        if use_pg:
            return _log_publication_feedback_postgres(
                week_start=week_start,
                week_end=week_end,
                publication_id=publication_id,
                vote=vote,
                source_ip=source_ip,
                user_agent=user_agent,
                context=context,
                database_url=database_url,
            )
        return _log_publication_feedback_sqlite(
            week_start=week_start,
            week_end=week_end,
            publication_id=publication_id,
            vote=vote,
            source_ip=source_ip,
            user_agent=user_agent,
            context=context,
            db_path=db_path,
        )
    except Exception as e:
        logger.warning("Failed to log publication feedback: %s", e)
        return False


def _log_publication_feedback_postgres(
    week_start: str,
    week_end: str,
    publication_id: str,
    vote: str,
    source_ip: Optional[str],
    user_agent: Optional[str],
    context: Optional[Dict[str, Any]],
    database_url: Optional[str],
) -> bool:
    """Log publication feedback to PostgreSQL."""
    conn = _get_postgres_connection(database_url)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS weekly_digest_feedback (
                id BIGSERIAL PRIMARY KEY,
                created_at TIMESTAMP DEFAULT NOW(),
                week_start DATE NOT NULL,
                week_end DATE NOT NULL,
                publication_id TEXT NOT NULL,
                vote TEXT NOT NULL,
                source_ip TEXT,
                user_agent TEXT,
                context_json TEXT
            )
        """)
        cursor.execute("""
            INSERT INTO weekly_digest_feedback (
                week_start, week_end, publication_id, vote,
                source_ip, user_agent, context_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            week_start,
            week_end,
            publication_id,
            vote,
            source_ip,
            user_agent,
            json.dumps(context or {}),
        ))
        conn.commit()
        cursor.close()
        return True
    except Exception as e:
        logger.error("Failed to log feedback to PostgreSQL: %s", e)
        conn.rollback()
        return False
    finally:
        conn.close()


def _log_publication_feedback_sqlite(
    week_start: str,
    week_end: str,
    publication_id: str,
    vote: str,
    source_ip: Optional[str],
    user_agent: Optional[str],
    context: Optional[Dict[str, Any]],
    db_path: Optional[str],
) -> bool:
    """Log publication feedback to SQLite."""
    conn = _get_sqlite_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS weekly_digest_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT (datetime('now')),
                week_start TEXT NOT NULL,
                week_end TEXT NOT NULL,
                publication_id TEXT NOT NULL,
                vote TEXT NOT NULL CHECK (vote IN ('up', 'down')),
                source_ip TEXT,
                user_agent TEXT,
                context_json TEXT
            )
        """)
        cursor.execute("""
            INSERT INTO weekly_digest_feedback (
                week_start, week_end, publication_id, vote,
                source_ip, user_agent, context_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            week_start,
            week_end,
            publication_id,
            vote,
            source_ip,
            user_agent,
            json.dumps(context or {}),
        ))
        conn.commit()
        cursor.close()
        return True
    except Exception as e:
        logger.error("Failed to log feedback to SQLite: %s", e)
        return False
    finally:
        conn.close()
