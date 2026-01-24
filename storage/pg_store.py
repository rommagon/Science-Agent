"""PostgreSQL storage for acitrack publications.

This module provides persistent storage for all fetched publications using PostgreSQL,
enabling future trend analysis and historical queries.

The database operations are non-blocking - if operations fail, the pipeline continues
with a warning.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

import psycopg2
from psycopg2.extras import execute_values
from psycopg2 import pool

from acitrack_types import Publication

logger = logging.getLogger(__name__)

# Connection pool (initialized lazily)
_connection_pool = None


def _get_connection_pool(database_url: str) -> pool.SimpleConnectionPool:
    """Get or create connection pool.

    Args:
        database_url: PostgreSQL connection URL

    Returns:
        Connection pool instance
    """
    global _connection_pool

    if _connection_pool is None:
        try:
            _connection_pool = pool.SimpleConnectionPool(
                minconn=1,
                maxconn=10,
                dsn=database_url
            )
            logger.info("PostgreSQL connection pool initialized")
        except Exception as e:
            logger.error("Failed to create connection pool: %s", e)
            raise

    return _connection_pool


def _get_connection(database_url: str):
    """Get a connection from the pool.

    Args:
        database_url: PostgreSQL connection URL

    Returns:
        Database connection
    """
    pool_instance = _get_connection_pool(database_url)
    return pool_instance.getconn()


def _put_connection(conn):
    """Return a connection to the pool.

    Args:
        conn: Database connection
    """
    global _connection_pool
    if _connection_pool:
        _connection_pool.putconn(conn)


def store_publications(
    publications: List[Publication],
    run_id: str,
    database_url: str,
) -> dict:
    """Store publications in the PostgreSQL database.

    This function is idempotent - duplicate publications (same ID) are updated.
    If the database operation fails, the function logs a warning and returns
    error information without raising an exception.

    Args:
        publications: List of Publication objects to store
        run_id: Run identifier for this batch
        database_url: PostgreSQL connection URL

    Returns:
        Dictionary with storage statistics:
        {
            "success": bool,
            "total": int,
            "inserted": int,
            "duplicates": int,
            "error": str or None
        }
    """
    if not publications:
        logger.info("No publications to store")
        return {
            "success": True,
            "total": 0,
            "inserted": 0,
            "duplicates": 0,
            "error": None,
        }

    conn = None
    try:
        conn = _get_connection(database_url)
        cursor = conn.cursor()

        inserted = 0
        duplicates = 0

        for pub in publications:
            try:
                authors_str = ", ".join(pub.authors) if pub.authors else ""
                source_names_str = ", ".join(pub.source_names) if getattr(pub, "source_names", None) else ""

                cursor.execute("""
                    INSERT INTO papers (
                        id, title, authors, source, venue, published_at,
                        url, raw_text, summary, run_id, source_names
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (
                    pub.id,
                    pub.title,
                    authors_str,
                    pub.source,
                    getattr(pub, "venue", None),
                    getattr(pub, "date", None),
                    getattr(pub, "url", None),
                    getattr(pub, "raw_text", None),
                    getattr(pub, "summary", None),
                    run_id,
                    source_names_str,
                ))

                if cursor.rowcount > 0:
                    inserted += 1
                else:
                    duplicates += 1

            except Exception as e:
                logger.warning("Failed to insert publication %s: %s", getattr(pub, "id", "UNKNOWN"), e)
                duplicates += 1
                continue

        conn.commit()

        logger.info(
            "Stored publications to database: %d total, %d inserted, %d duplicates",
            len(publications),
            inserted,
            duplicates,
        )

        return {
            "success": True,
            "total": len(publications),
            "inserted": inserted,
            "duplicates": duplicates,
            "error": None,
        }

    except Exception as e:
        logger.error("Failed to store publications: %s", e)
        if conn:
            conn.rollback()
        return {
            "success": False,
            "total": len(publications) if publications else 0,
            "inserted": 0,
            "duplicates": 0,
            "error": str(e),
        }
    finally:
        if conn:
            cursor.close()
            _put_connection(conn)


def store_run_history(
    run_id: str,
    started_at: str,
    since_timestamp: str,
    sources_count: int,
    total_fetched: int,
    total_deduped: int,
    new_count: int,
    unchanged_count: int,
    summarized_count: int,
    max_items_per_source: Optional[int] = None,
    upload_drive: bool = False,
    publications_with_status: Optional[List[dict]] = None,
    database_url: str = None,
) -> dict:
    """Store run history metadata and run_papers associations.

    Args:
        run_id: Run identifier
        started_at: Run start timestamp
        since_timestamp: Lookback timestamp
        sources_count: Number of sources processed
        total_fetched: Total publications fetched
        total_deduped: Total after deduplication
        new_count: Count of new publications
        unchanged_count: Count of unchanged publications
        summarized_count: Count of summarized publications
        max_items_per_source: Max items per source (optional)
        upload_drive: Whether Drive upload was enabled
        publications_with_status: List of publication dicts with status field
        database_url: PostgreSQL connection URL

    Returns:
        Dictionary with storage result:
        {
            "success": bool,
            "error": str or None,
            "pub_runs_inserted": int
        }
    """
    conn = None
    try:
        conn = _get_connection(database_url)
        cursor = conn.cursor()

        # Store run metadata
        cursor.execute("""
            INSERT INTO runs (
                run_id, started_at, since_timestamp, max_items_per_source,
                sources_count, total_fetched, total_deduped, new_count,
                unchanged_count, summarized_count, upload_drive
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id) DO UPDATE SET
                started_at = EXCLUDED.started_at,
                since_timestamp = EXCLUDED.since_timestamp,
                max_items_per_source = EXCLUDED.max_items_per_source,
                sources_count = EXCLUDED.sources_count,
                total_fetched = EXCLUDED.total_fetched,
                total_deduped = EXCLUDED.total_deduped,
                new_count = EXCLUDED.new_count,
                unchanged_count = EXCLUDED.unchanged_count,
                summarized_count = EXCLUDED.summarized_count,
                upload_drive = EXCLUDED.upload_drive
        """, (
            run_id,
            started_at,
            since_timestamp,
            max_items_per_source,
            sources_count,
            total_fetched,
            total_deduped,
            new_count,
            unchanged_count,
            summarized_count,
            upload_drive,
        ))

        # Store run_papers associations if provided
        pub_runs_inserted = 0
        if publications_with_status:
            run_papers_data = []
            for pub in publications_with_status:
                run_papers_data.append((
                    run_id,
                    pub.get("id"),
                    pub.get("status", "UNKNOWN"),
                    pub.get("source"),
                    pub.get("date"),
                ))

            execute_values(cursor, """
                INSERT INTO run_papers (run_id, pub_id, status, source, published_at)
                VALUES %s
                ON CONFLICT (run_id, pub_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    source = EXCLUDED.source,
                    published_at = EXCLUDED.published_at
            """, run_papers_data)

            pub_runs_inserted = len(run_papers_data)

        conn.commit()

        logger.info("Stored run history for run_id=%s (%d pub_runs)", run_id, pub_runs_inserted)

        return {
            "success": True,
            "error": None,
            "pub_runs_inserted": pub_runs_inserted,
        }

    except Exception as e:
        logger.warning("Failed to store run history: %s (non-blocking)", e)
        if conn:
            conn.rollback()
        return {
            "success": False,
            "error": str(e),
            "pub_runs_inserted": 0,
        }
    finally:
        if conn:
            cursor.close()
            _put_connection(conn)


def get_run_history(limit: int = 10, database_url: str = None) -> list:
    """Get recent run history.

    Args:
        limit: Maximum number of runs to return
        database_url: PostgreSQL connection URL

    Returns:
        List of run dictionaries, or empty list if query fails
    """
    conn = None
    try:
        conn = _get_connection(database_url)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                run_id, started_at, since_timestamp, max_items_per_source,
                sources_count, total_fetched, total_deduped, new_count,
                unchanged_count, summarized_count, upload_drive
            FROM runs
            ORDER BY started_at DESC
            LIMIT %s
        """, (limit,))

        results = []
        for row in cursor.fetchall():
            results.append({
                "run_id": row[0],
                "started_at": row[1],
                "since_timestamp": row[2],
                "max_items_per_source": row[3],
                "sources_count": row[4],
                "total_fetched": row[5],
                "total_deduped": row[6],
                "new_count": row[7],
                "unchanged_count": row[8],
                "summarized_count": row[9],
                "upload_drive": row[10],
            })

        return results

    except Exception as e:
        logger.warning("Failed to get run history: %s", e)
        return []
    finally:
        if conn:
            cursor.close()
            _put_connection(conn)


def store_relevancy_scoring_event(
    run_id: str,
    mode: str,
    publication_id: str,
    source: str,
    prompt_version: str,
    model: str,
    relevancy_score: Optional[int],
    relevancy_reason: Optional[str],
    confidence: Optional[str],
    signals: Optional[Dict],
    input_fingerprint: Optional[str],
    raw_response: Optional[Dict],
    latency_ms: Optional[int],
    cost_usd: Optional[float],
    database_url: str = None,
) -> dict:
    """Store a relevancy scoring event.

    Args:
        run_id: Run identifier
        mode: Run mode (daily, weekly, etc.)
        publication_id: Publication ID
        source: Source name
        prompt_version: Prompt version used
        model: Model name used
        relevancy_score: Relevancy score (0-100)
        relevancy_reason: Reason for score
        confidence: Confidence level
        signals: Extracted signals dict
        input_fingerprint: Hash of input
        raw_response: Raw API response
        latency_ms: Latency in milliseconds
        cost_usd: Cost in USD
        database_url: PostgreSQL connection URL

    Returns:
        Dictionary with storage result
    """
    conn = None
    try:
        conn = _get_connection(database_url)
        cursor = conn.cursor()

        signals_json = json.dumps(signals) if signals else None
        raw_response_json = json.dumps(raw_response) if raw_response else None

        cursor.execute("""
            INSERT INTO relevancy_events (
                run_id, mode, publication_id, source, prompt_version, model,
                relevancy_score, relevancy_reason, confidence, signals_json,
                input_fingerprint, raw_response_json, latency_ms, cost_usd
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, publication_id, prompt_version) DO UPDATE SET
                mode = EXCLUDED.mode,
                source = EXCLUDED.source,
                model = EXCLUDED.model,
                relevancy_score = EXCLUDED.relevancy_score,
                relevancy_reason = EXCLUDED.relevancy_reason,
                confidence = EXCLUDED.confidence,
                signals_json = EXCLUDED.signals_json,
                input_fingerprint = EXCLUDED.input_fingerprint,
                raw_response_json = EXCLUDED.raw_response_json,
                latency_ms = EXCLUDED.latency_ms,
                cost_usd = EXCLUDED.cost_usd,
                created_at = CURRENT_TIMESTAMP
        """, (
            run_id,
            mode,
            publication_id,
            source,
            prompt_version,
            model,
            relevancy_score,
            relevancy_reason,
            confidence,
            signals_json,
            input_fingerprint,
            raw_response_json,
            latency_ms,
            cost_usd,
        ))

        conn.commit()

        logger.debug(
            "Stored relevancy event: run_id=%s, pub_id=%s, score=%s",
            run_id,
            publication_id[:16],
            relevancy_score,
        )

        return {
            "success": True,
            "error": None,
        }

    except Exception as e:
        logger.warning("Failed to store relevancy event: %s", e)
        if conn:
            conn.rollback()
        return {
            "success": False,
            "error": str(e),
        }
    finally:
        if conn:
            cursor.close()
            _put_connection(conn)


def get_relevancy_scores_for_run(
    run_id: str,
    database_url: str = None,
) -> Dict[str, Dict]:
    """Get all relevancy scores for a run.

    Args:
        run_id: Run identifier
        database_url: PostgreSQL connection URL

    Returns:
        Dictionary mapping publication_id to score data
    """
    conn = None
    try:
        conn = _get_connection(database_url)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                publication_id, relevancy_score, relevancy_reason, confidence,
                signals_json, prompt_version, model, created_at
            FROM relevancy_events
            WHERE run_id = %s
            ORDER BY created_at ASC
        """, (run_id,))

        results = {}
        for row in cursor.fetchall():
            pub_id = row[0]
            signals = json.loads(row[4]) if row[4] else {}

            results[pub_id] = {
                "relevancy_score": row[1],
                "relevancy_reason": row[2],
                "confidence": row[3],
                "signals": signals,
                "prompt_version": row[5],
                "model": row[6],
                "created_at": row[7],
            }

        logger.debug("Retrieved %d relevancy scores for run_id=%s", len(results), run_id)
        return results

    except Exception as e:
        logger.warning("Failed to get relevancy scores for run %s: %s", run_id, e)
        return {}
    finally:
        if conn:
            cursor.close()
            _put_connection(conn)


def export_relevancy_events_to_jsonl(
    run_id: str,
    output_path: str,
    database_url: str = None,
) -> dict:
    """Export relevancy scoring events for a run to JSONL file.

    Args:
        run_id: Run identifier to export
        output_path: Path to output JSONL file
        database_url: PostgreSQL connection URL

    Returns:
        Dictionary with export statistics
    """
    conn = None
    try:
        conn = _get_connection(database_url)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                run_id, mode, publication_id, source, prompt_version, model,
                created_at, relevancy_score, relevancy_reason, confidence,
                signals_json, input_fingerprint, latency_ms, cost_usd
            FROM relevancy_events
            WHERE run_id = %s
            ORDER BY created_at ASC
        """, (run_id,))

        # Ensure output directory exists
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        events_exported = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for row in cursor.fetchall():
                event = {
                    "run_id": row[0],
                    "mode": row[1],
                    "publication_id": row[2],
                    "source": row[3],
                    "prompt_version": row[4],
                    "model": row[5],
                    "created_at": row[6].isoformat() if row[6] else None,
                    "relevancy_score": row[7],
                    "relevancy_reason": row[8],
                    "confidence": row[9],
                    "signals": json.loads(row[10]) if row[10] else {},
                    "input_fingerprint": row[11],
                    "latency_ms": row[12],
                    "cost_usd": row[13],
                }
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
                events_exported += 1

        logger.info("Exported %d relevancy events to %s", events_exported, output_path)

        return {
            "success": True,
            "events_exported": events_exported,
            "output_path": output_path,
            "error": None,
        }

    except Exception as e:
        logger.warning("Failed to export relevancy events: %s", e)
        return {
            "success": False,
            "events_exported": 0,
            "output_path": output_path,
            "error": str(e),
        }
    finally:
        if conn:
            cursor.close()
            _put_connection(conn)


def store_tri_model_scoring_event(
    run_id: str,
    mode: str,
    publication_id: str,
    title: str,
    source: str,
    published_date: Optional[str],
    claude_review: Optional[Dict],
    gemini_review: Optional[Dict],
    gpt_eval: Optional[Dict],
    final_relevancy_score: int,
    final_relevancy_reason: str,
    final_signals: Dict,
    final_summary: str,
    agreement_level: str,
    disagreements: str,
    evaluator_rationale: str,
    confidence: str,
    prompt_versions: Dict,
    model_names: Dict,
    claude_latency_ms: Optional[int],
    gemini_latency_ms: Optional[int],
    gpt_latency_ms: Optional[int],
    database_url: str = None,
) -> dict:
    """Store a tri-model scoring event.

    Args:
        run_id: Run identifier
        mode: Run mode (tri-model-daily, etc.)
        publication_id: Publication ID
        title: Publication title
        source: Source name
        published_date: Publication date
        claude_review: Claude review dict or None
        gemini_review: Gemini review dict or None
        gpt_eval: GPT evaluation dict or None
        final_relevancy_score: Final score (0-100)
        final_relevancy_reason: Final reason
        final_signals: Final signals dict
        final_summary: Final summary
        agreement_level: Agreement level (high/moderate/low)
        disagreements: Disagreements text
        evaluator_rationale: Evaluator rationale
        confidence: Confidence level
        prompt_versions: Prompt versions dict
        model_names: Model names dict
        claude_latency_ms: Claude latency
        gemini_latency_ms: Gemini latency
        gpt_latency_ms: GPT latency
        database_url: PostgreSQL connection URL

    Returns:
        Dictionary with storage result
    """
    conn = None
    try:
        conn = _get_connection(database_url)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO tri_model_events (
                run_id, mode, publication_id, title, source, published_date,
                claude_review_json, gemini_review_json, gpt_eval_json,
                final_relevancy_score, final_relevancy_reason, final_signals_json,
                final_summary, agreement_level, disagreements, evaluator_rationale,
                confidence, prompt_versions_json, model_names_json,
                claude_latency_ms, gemini_latency_ms, gpt_latency_ms
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, publication_id) DO UPDATE SET
                mode = EXCLUDED.mode,
                title = EXCLUDED.title,
                source = EXCLUDED.source,
                published_date = EXCLUDED.published_date,
                claude_review_json = EXCLUDED.claude_review_json,
                gemini_review_json = EXCLUDED.gemini_review_json,
                gpt_eval_json = EXCLUDED.gpt_eval_json,
                final_relevancy_score = EXCLUDED.final_relevancy_score,
                final_relevancy_reason = EXCLUDED.final_relevancy_reason,
                final_signals_json = EXCLUDED.final_signals_json,
                final_summary = EXCLUDED.final_summary,
                agreement_level = EXCLUDED.agreement_level,
                disagreements = EXCLUDED.disagreements,
                evaluator_rationale = EXCLUDED.evaluator_rationale,
                confidence = EXCLUDED.confidence,
                prompt_versions_json = EXCLUDED.prompt_versions_json,
                model_names_json = EXCLUDED.model_names_json,
                claude_latency_ms = EXCLUDED.claude_latency_ms,
                gemini_latency_ms = EXCLUDED.gemini_latency_ms,
                gpt_latency_ms = EXCLUDED.gpt_latency_ms,
                created_at = CURRENT_TIMESTAMP
        """, (
            run_id,
            mode,
            publication_id,
            title,
            source,
            published_date,
            json.dumps(claude_review) if claude_review else None,
            json.dumps(gemini_review) if gemini_review else None,
            json.dumps(gpt_eval) if gpt_eval else None,
            final_relevancy_score,
            final_relevancy_reason,
            json.dumps(final_signals),
            final_summary,
            agreement_level,
            disagreements,
            evaluator_rationale,
            confidence,
            json.dumps(prompt_versions),
            json.dumps(model_names),
            claude_latency_ms,
            gemini_latency_ms,
            gpt_latency_ms,
        ))

        conn.commit()

        logger.debug(
            "Stored tri-model event: run_id=%s, pub_id=%s, score=%s",
            run_id,
            publication_id[:16],
            final_relevancy_score,
        )

        return {
            "success": True,
            "error": None,
        }

    except Exception as e:
        logger.warning("Failed to store tri-model event: %s", e)
        if conn:
            conn.rollback()
        return {
            "success": False,
            "error": str(e),
        }
    finally:
        if conn:
            cursor.close()
            _put_connection(conn)


def export_tri_model_events_to_jsonl(
    run_id: str,
    output_path: str,
    database_url: str = None,
) -> dict:
    """Export tri-model scoring events for a run to JSONL file.

    Args:
        run_id: Run identifier to export
        output_path: Path to output JSONL file
        database_url: PostgreSQL connection URL

    Returns:
        Dictionary with export statistics
    """
    conn = None
    try:
        conn = _get_connection(database_url)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                run_id, mode, publication_id, title, source, published_date,
                claude_review_json, gemini_review_json, gpt_eval_json,
                final_relevancy_score, final_relevancy_reason, final_signals_json,
                final_summary, agreement_level, disagreements, evaluator_rationale,
                confidence, prompt_versions_json, model_names_json,
                claude_latency_ms, gemini_latency_ms, gpt_latency_ms,
                created_at
            FROM tri_model_events
            WHERE run_id = %s
            ORDER BY created_at ASC
        """, (run_id,))

        # Ensure output directory exists
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        events_exported = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for row in cursor.fetchall():
                event = {
                    "run_id": row[0],
                    "mode": row[1],
                    "publication_id": row[2],
                    "title": row[3],
                    "source": row[4],
                    "published_date": row[5],
                    "claude_review": json.loads(row[6]) if row[6] else None,
                    "gemini_review": json.loads(row[7]) if row[7] else None,
                    "gpt_eval": json.loads(row[8]) if row[8] else None,
                    "final_relevancy_score": row[9],
                    "final_relevancy_reason": row[10],
                    "final_signals": json.loads(row[11]) if row[11] else {},
                    "final_summary": row[12],
                    "agreement_level": row[13],
                    "disagreements": row[14],
                    "evaluator_rationale": row[15],
                    "confidence": row[16],
                    "prompt_versions": json.loads(row[17]) if row[17] else {},
                    "model_names": json.loads(row[18]) if row[18] else {},
                    "claude_latency_ms": row[19],
                    "gemini_latency_ms": row[20],
                    "gpt_latency_ms": row[21],
                    "created_at": row[22].isoformat() if row[22] else None,
                }
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
                events_exported += 1

        logger.info("Exported %d tri-model events to %s", events_exported, output_path)

        return {
            "success": True,
            "events_exported": events_exported,
            "output_path": output_path,
            "error": None,
        }

    except Exception as e:
        logger.warning("Failed to export tri-model events: %s", e)
        return {
            "success": False,
            "events_exported": 0,
            "output_path": output_path,
            "error": str(e),
        }
    finally:
        if conn:
            cursor.close()
            _put_connection(conn)
