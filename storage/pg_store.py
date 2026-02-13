"""PostgreSQL storage for acitrack publications.

This module provides persistent storage for all fetched publications using PostgreSQL,
enabling future trend analysis and historical queries.

The database operations are non-blocking - if operations fail, the pipeline continues
with a warning.
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

import psycopg2
from psycopg2.extras import execute_values
from psycopg2 import pool

from acitrack_types import Publication

logger = logging.getLogger(__name__)

# Connection pool (initialized lazily)
_connection_pool = None
_publications_table_meta_cache: Dict[str, Tuple[set, str, bool]] = {}


def _get_publications_table_metadata(conn, database_url: str) -> Tuple[set, str, bool, bool]:
    """Get publications table metadata.

    Returns:
        (columns, pk_column, force_python_created_at, force_python_updated_at)
    """
    global _publications_table_meta_cache

    cache_key = database_url or "default"
    if cache_key in _publications_table_meta_cache:
        return _publications_table_meta_cache[cache_key]

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT column_name, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'publications'
        """
    )
    rows = cursor.fetchall()
    columns = {row[0] for row in rows}
    cursor.close()

    pk_column = ""
    for candidate in ("id", "publication_id", "pub_id"):
        if candidate in columns:
            pk_column = candidate
            break

    # If created_at/updated_at are NOT NULL with no default, application must provide them.
    force_python_created_at = False
    force_python_updated_at = False
    for row in rows:
        col_name, is_nullable, column_default = row
        if col_name == "created_at" and is_nullable == "NO" and column_default is None:
            force_python_created_at = True
        if col_name == "updated_at" and is_nullable == "NO" and column_default is None:
            force_python_updated_at = True

    logger.info(
        "PostgreSQL publications columns detected: %s",
        ", ".join(sorted(columns)),
    )
    logger.info("PostgreSQL publications PK column selected: %s", pk_column or "<none>")
    if force_python_created_at:
        logger.info("PostgreSQL publications.created_at requires app-side value (NOT NULL with no default)")
    if force_python_updated_at:
        logger.info("PostgreSQL publications.updated_at requires app-side value (NOT NULL with no default)")

    _publications_table_meta_cache[cache_key] = (columns, pk_column, force_python_created_at, force_python_updated_at)
    return columns, pk_column, force_python_created_at, force_python_updated_at


def _get_available_columns(conn, table_name: str, is_pg: bool = True) -> set:
    """Get set of column names for a table.

    Args:
        conn: Database connection
        table_name: Name of the table
        is_pg: Whether this is PostgreSQL (always True in pg_store)

    Returns:
        Set of column name strings
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
    """, (table_name,))
    columns = {row[0] for row in cursor.fetchall()}
    cursor.close()
    return columns


def _build_publications_insert_statement(
    table_columns: set,
    pk_column: str,
    force_python_created_at: bool = False,
    force_python_updated_at: bool = False,
) -> Tuple[str, List[str]]:
    """Build schema-tolerant INSERT for publications."""
    supported_fields = [
        pk_column,
        "title",
        "authors",
        "source",
        "venue",
        "published_at",
        "published_date",
        "url",
        "canonical_url",
        "doi",
        "pmid",
        "source_type",
        "raw_text",
        "abstract",
        "summary",
        "run_id",
        "source_names",
    ]
    if force_python_created_at:
        supported_fields.append("created_at")
    if force_python_updated_at:
        supported_fields.append("updated_at")
    insert_columns = [c for c in supported_fields if c and c in table_columns]
    placeholders = ", ".join(["%s"] * len(insert_columns))
    column_list = ", ".join(insert_columns)

    if pk_column and pk_column in table_columns:
        # On conflict, update URL-related fields so existing publications
        # get their links populated even if they were inserted before these
        # columns existed.  COALESCE(EXCLUDED.x, publications.x) ensures we
        # never overwrite a good value with NULL.
        upsert_fields = [
            c for c in ["url", "canonical_url", "doi", "pmid", "source_type"]
            if c in table_columns and c in insert_columns
        ]
        if upsert_fields:
            update_set = ", ".join(
                f"{c} = COALESCE(EXCLUDED.{c}, publications.{c})"
                for c in upsert_fields
            )
            sql = (
                f"INSERT INTO publications ({column_list}) VALUES ({placeholders}) "
                f"ON CONFLICT ({pk_column}) DO UPDATE SET {update_set}"
            )
        else:
            sql = (
                f"INSERT INTO publications ({column_list}) VALUES ({placeholders}) "
                f"ON CONFLICT ({pk_column}) DO NOTHING"
            )
    else:
        sql = f"INSERT INTO publications ({column_list}) VALUES ({placeholders})"

    return sql, insert_columns


def _map_publication_values(
    pub: Publication,
    run_id: str,
    pk_column: str,
    insert_columns: List[str],
    force_python_created_at: bool = False,
    force_python_updated_at: bool = False,
) -> List[Any]:
    """Map a Publication object to INSERT column values."""
    authors_str = ", ".join(pub.authors) if pub.authors else ""
    source_names_str = ", ".join(pub.source_names) if getattr(pub, "source_names", None) else ""
    pub_id = getattr(pub, "id", None)
    now = datetime.utcnow()
    created_at_value = now if force_python_created_at else None
    updated_at_value = now if force_python_updated_at else None
    values_map = {
        pk_column: pub_id,
        "title": getattr(pub, "title", None),
        "authors": authors_str,
        "source": getattr(pub, "source", None),
        "venue": getattr(pub, "venue", None),
        "published_at": getattr(pub, "date", None),
        "published_date": getattr(pub, "date", None),
        "url": getattr(pub, "url", None),
        "canonical_url": getattr(pub, "canonical_url", None),
        "doi": getattr(pub, "doi", None),
        "pmid": getattr(pub, "pmid", None),
        "source_type": getattr(pub, "source_type", None),
        "raw_text": getattr(pub, "raw_text", None),
        "abstract": getattr(pub, "raw_text", None),
        "summary": getattr(pub, "summary", None),
        "run_id": run_id,
        "source_names": source_names_str,
        "created_at": created_at_value,
        "updated_at": updated_at_value,
    }
    return [values_map.get(col) for col in insert_columns]


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
        table_columns, pk_column, force_python_created_at, force_python_updated_at = _get_publications_table_metadata(conn, database_url)
        if not pk_column:
            raise RuntimeError("Could not find publications PK column (expected id/publication_id/pub_id)")
        insert_sql, insert_columns = _build_publications_insert_statement(
            table_columns,
            pk_column,
            force_python_created_at=force_python_created_at,
            force_python_updated_at=force_python_updated_at,
        )

        inserted = 0
        duplicates = 0

        for pub in publications:
            try:
                values = _map_publication_values(
                    pub,
                    run_id,
                    pk_column,
                    insert_columns,
                    force_python_created_at=force_python_created_at,
                    force_python_updated_at=force_python_updated_at,
                )

                cursor.execute("SAVEPOINT pub_insert_sp")
                cursor.execute(insert_sql, values)
                cursor.execute("RELEASE SAVEPOINT pub_insert_sp")

                if cursor.rowcount > 0:
                    inserted += 1
                else:
                    duplicates += 1

            except Exception as e:
                logger.warning("Failed to insert publication %s: %s", getattr(pub, "id", "UNKNOWN"), e)
                try:
                    cursor.execute("ROLLBACK TO SAVEPOINT pub_insert_sp")
                    cursor.execute("RELEASE SAVEPOINT pub_insert_sp")
                except Exception:
                    conn.rollback()
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


# Cache for tri_model_events table columns
_tri_model_events_columns_cache: Dict[str, set] = {}


def _get_tri_model_events_columns(conn, database_url: str) -> set:
    """Get available columns in tri_model_events table."""
    global _tri_model_events_columns_cache

    cache_key = database_url or "default"
    if cache_key in _tri_model_events_columns_cache:
        return _tri_model_events_columns_cache[cache_key]

    cursor = conn.cursor()
    cursor.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'tri_model_events'
    """)
    columns = {row[0] for row in cursor.fetchall()}
    cursor.close()

    logger.info("tri_model_events columns detected: %s", ", ".join(sorted(columns)))
    _tri_model_events_columns_cache[cache_key] = columns
    return columns


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
    credibility_score: Optional[int] = None,
    credibility_reason: Optional[str] = None,
    credibility_confidence: Optional[str] = None,
    credibility_signals: Optional[Dict] = None,
    url: Optional[str] = None,
    database_url: str = None,
) -> dict:
    """Store a tri-model scoring event (schema-tolerant).

    Dynamically detects available columns in the tri_model_events table
    and only inserts columns that exist.

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
        credibility_score: Credibility score (optional)
        credibility_reason: Credibility reason (optional)
        credibility_confidence: Credibility confidence (optional)
        credibility_signals: Credibility signals (optional)
        url: Publication URL (optional)
        database_url: PostgreSQL connection URL

    Returns:
        Dictionary with storage result
    """
    conn = None
    try:
        conn = _get_connection(database_url)

        # Get available columns in table
        available_columns = _get_tri_model_events_columns(conn, database_url)

        # Normalize disagreements to string (handle list/dict from evaluator)
        if isinstance(disagreements, (list, dict)):
            disagreements_str = json.dumps(disagreements, ensure_ascii=False)
        elif disagreements is None:
            disagreements_str = None
        else:
            disagreements_str = str(disagreements)

        # Build column -> value mapping for all possible columns
        all_columns = {
            "run_id": run_id,
            "mode": mode,
            "publication_id": publication_id,
            "title": title,
            "source": source,
            "published_date": published_date,
            "claude_review_json": json.dumps(claude_review, ensure_ascii=False) if claude_review else None,
            "gemini_review_json": json.dumps(gemini_review, ensure_ascii=False) if gemini_review else None,
            "gpt_eval_json": json.dumps(gpt_eval, ensure_ascii=False) if gpt_eval else None,
            "final_relevancy_score": final_relevancy_score,
            "final_relevancy_reason": final_relevancy_reason,
            "final_signals_json": json.dumps(final_signals, ensure_ascii=False) if final_signals else None,
            "final_summary": final_summary,
            "agreement_level": agreement_level,
            "disagreements": disagreements_str,
            "evaluator_rationale": evaluator_rationale,
            "confidence": confidence,
            "prompt_versions_json": json.dumps(prompt_versions, ensure_ascii=False) if prompt_versions else None,
            "model_names_json": json.dumps(model_names, ensure_ascii=False) if model_names else None,
            "claude_latency_ms": claude_latency_ms,
            "gemini_latency_ms": gemini_latency_ms,
            "gpt_latency_ms": gpt_latency_ms,
            # URL and credibility fields (added in migration 002)
            "url": url,
            "credibility_score": credibility_score,
            "credibility_reason": credibility_reason,
            "credibility_confidence": credibility_confidence,
            "credibility_signals_json": json.dumps(credibility_signals, ensure_ascii=False) if credibility_signals else None,
        }

        # Filter to only columns that exist in the table
        # Always include these core columns (they should exist)
        core_columns = ["run_id", "mode", "publication_id"]
        insert_columns = []
        insert_values = []

        for col, val in all_columns.items():
            if col in available_columns:
                insert_columns.append(col)
                insert_values.append(val)

        # Add created_at if it exists
        if "created_at" in available_columns:
            insert_columns.append("created_at")
            insert_values.append(None)  # Will use NOW() in SQL

        # Build dynamic SQL
        col_list = ", ".join(insert_columns)
        placeholders = ", ".join(
            "NOW()" if col == "created_at" else "%s"
            for col in insert_columns
        )

        # Build ON CONFLICT update clause (exclude created_at from values list)
        update_cols = [c for c in insert_columns if c not in ("run_id", "publication_id", "created_at")]
        update_clause = ", ".join(
            f"{col} = EXCLUDED.{col}" for col in update_cols
        )
        if "created_at" in available_columns:
            update_clause += ", created_at = CURRENT_TIMESTAMP"

        # Remove None for created_at from values (since we use NOW())
        final_values = [v for c, v in zip(insert_columns, insert_values) if c != "created_at"]

        cursor = conn.cursor()

        sql = f"""
            INSERT INTO tri_model_events ({col_list})
            VALUES ({placeholders})
            ON CONFLICT (run_id, publication_id) DO UPDATE SET
                {update_clause}
        """

        cursor.execute(sql, final_values)
        conn.commit()

        logger.debug(
            "Stored tri-model event: run_id=%s, pub_id=%s, score=%s (cols=%d)",
            run_id,
            publication_id[:16],
            final_relevancy_score,
            len(insert_columns),
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
    """Export tri-model scoring events for a run to JSONL file (schema-tolerant).

    Dynamically detects available columns in the tri_model_events table
    and only selects columns that exist.

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

        # Get available columns in table
        available_columns = _get_tri_model_events_columns(conn, database_url)

        # Define all desired columns and their JSON parsing needs
        # Order matters for building the event dict
        desired_columns = [
            ("run_id", False),
            ("mode", False),
            ("publication_id", False),
            ("title", False),
            ("source", False),
            ("published_date", False),
            ("url", False),
            ("claude_review_json", True),
            ("gemini_review_json", True),
            ("gpt_eval_json", True),
            ("final_relevancy_score", False),
            ("final_relevancy_reason", False),
            ("final_signals_json", True),
            ("final_summary", False),
            ("agreement_level", False),
            ("disagreements", False),
            ("evaluator_rationale", False),
            ("confidence", False),
            ("prompt_versions_json", True),
            ("model_names_json", True),
            ("claude_latency_ms", False),
            ("gemini_latency_ms", False),
            ("gpt_latency_ms", False),
            ("credibility_score", False),
            ("credibility_reason", False),
            ("credibility_confidence", False),
            ("credibility_signals_json", True),
            ("created_at", False),
        ]

        # Detect which columns exist in the publications table so we can
        # LEFT JOIN for fallback values (url, published_date, etc.)
        pub_columns = _get_available_columns(conn, "publications", True)
        # Detect the PK column in publications for the JOIN condition
        pub_pk = None
        for candidate in ("publication_id", "id", "pub_id"):
            if candidate in pub_columns:
                pub_pk = candidate
                break

        # Columns where we COALESCE from the publications table as fallback
        coalesce_map = {}  # event_col -> (pub_col, pub_col_exists)
        if pub_pk:
            for event_col, pub_col in [("url", "url"), ("published_date", "published_date")]:
                if pub_col in pub_columns:
                    coalesce_map[event_col] = pub_col

        # Output key mapping for _json suffixed columns
        json_key_map = {
            "final_signals_json": "final_signals",
            "prompt_versions_json": "prompt_versions",
            "model_names_json": "model_names",
            "claude_review_json": "claude_review",
            "gemini_review_json": "gemini_review",
            "gpt_eval_json": "gpt_eval",
            "credibility_signals_json": "credibility_signals",
        }

        # Filter to only columns that exist in the events table
        select_expressions = []
        column_meta = []  # (output_key, is_json, is_datetime)
        for col, is_json in desired_columns:
            if col in available_columns:
                # Use COALESCE with publications fallback where applicable
                if col in coalesce_map:
                    pub_col = coalesce_map[col]
                    select_expressions.append(
                        f"COALESCE(e.{col}, p.{pub_col}) AS {col}"
                    )
                else:
                    select_expressions.append(f"e.{col}")
                output_key = json_key_map.get(col, col)
                is_datetime = col == "created_at"
                column_meta.append((output_key, is_json, is_datetime))
            elif col in coalesce_map:
                # Column doesn't exist in events table yet, but we can
                # still pull it from publications as a pure fallback
                pub_col = coalesce_map[col]
                select_expressions.append(f"p.{pub_col} AS {col}")
                output_key = json_key_map.get(col, col)
                is_datetime = col == "created_at"
                column_meta.append((output_key, is_json, is_datetime))

        if not select_expressions:
            logger.warning("No columns available for export")
            return {
                "success": False,
                "events_exported": 0,
                "output_path": output_path,
                "error": "No columns available for export",
            }

        cursor = conn.cursor()
        col_list = ", ".join(select_expressions)

        # LEFT JOIN with publications for fallback values
        if pub_pk and coalesce_map:
            cursor.execute(f"""
                SELECT {col_list}
                FROM tri_model_events e
                LEFT JOIN publications p ON e.publication_id = p.{pub_pk}
                WHERE e.run_id = %s
                ORDER BY e.created_at ASC
            """, (run_id,))
        else:
            cursor.execute(f"""
                SELECT {col_list}
                FROM tri_model_events e
                WHERE e.run_id = %s
                ORDER BY e.created_at ASC
            """, (run_id,))

        # Ensure output directory exists
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        events_exported = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for row in cursor.fetchall():
                event = {}
                for i, (output_key, is_json, is_datetime) in enumerate(column_meta):
                    val = row[i]
                    if val is None:
                        event[output_key] = None
                    elif is_json:
                        event[output_key] = json.loads(val) if val else None
                    elif is_datetime:
                        event[output_key] = val.isoformat() if val else None
                    else:
                        event[output_key] = val
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
                events_exported += 1

        logger.info("Exported %d tri-model events to %s (cols=%d)", events_exported, output_path, len(column_meta))

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


def update_publication_scoring(
    publication_id: str,
    final_relevancy_score: int,
    final_relevancy_reason: str,
    final_summary: str,
    agreement_level: str,
    confidence: str,
    credibility_score: Optional[int] = None,
    credibility_reason: Optional[str] = None,
    credibility_confidence: Optional[str] = None,
    credibility_signals: Optional[Dict] = None,
    claude_score: Optional[int] = None,
    gemini_score: Optional[int] = None,
    evaluator_rationale: Optional[str] = None,
    disagreements=None,
    final_signals: Optional[Dict] = None,
    scoring_run_id: Optional[str] = None,
    database_url: str = None,
) -> dict:
    """Write scoring results directly to the publications row.

    This makes publications the single source of truth for scoring data.
    Schema-tolerant: only updates columns that exist in the table.

    Args:
        publication_id: Publication ID to update
        final_relevancy_score: Final tri-model score (0-100)
        final_relevancy_reason: Reason for the score
        final_summary: Synthesized summary
        agreement_level: Reviewer agreement (high/moderate/low)
        confidence: Confidence level
        credibility_score: Credibility score (0-100)
        credibility_reason: Credibility rationale
        credibility_confidence: Credibility confidence (low/medium/high)
        credibility_signals: Credibility signals dict
        claude_score: Individual Claude reviewer score
        gemini_score: Individual Gemini reviewer score
        evaluator_rationale: GPT evaluator rationale
        disagreements: Reviewer disagreements (str, list, or dict)
        final_signals: Final signals dict
        scoring_run_id: Which run produced these scores
        database_url: PostgreSQL connection URL

    Returns:
        Dictionary with update result
    """
    conn = None
    try:
        conn = _get_connection(database_url)

        # Get table metadata (columns + PK)
        table_columns, pk_column, _, _ = _get_publications_table_metadata(conn, database_url)
        pk_col = pk_column or "publication_id"

        # Normalize disagreements
        if isinstance(disagreements, (list, dict)):
            disagreements_str = json.dumps(disagreements, ensure_ascii=False)
        elif disagreements is None:
            disagreements_str = None
        else:
            disagreements_str = str(disagreements)

        # Build column -> value mapping for all scoring columns
        all_updates = {
            "final_relevancy_score": final_relevancy_score,
            "final_relevancy_reason": final_relevancy_reason,
            "final_summary": final_summary,
            "agreement_level": agreement_level,
            "confidence": confidence,
            "credibility_score": credibility_score,
            "credibility_reason": credibility_reason,
            "credibility_confidence": credibility_confidence,
            "credibility_signals_json": json.dumps(credibility_signals, ensure_ascii=False) if credibility_signals else None,
            "claude_score": claude_score,
            "gemini_score": gemini_score,
            "evaluator_rationale": evaluator_rationale,
            "disagreements": disagreements_str,
            "final_signals_json": json.dumps(final_signals, ensure_ascii=False) if final_signals else None,
            "scoring_run_id": scoring_run_id,
        }

        # Filter to only columns that exist in the table
        update_pairs = {k: v for k, v in all_updates.items() if k in table_columns}

        if not update_pairs:
            logger.warning(
                "No scoring columns found in publications table for update (pub_id=%s). "
                "Run Alembic migration 003 to add scoring columns.",
                publication_id[:16],
            )
            return {"success": True, "updated": False, "error": None}

        # Add scoring_updated_at if column exists
        if "scoring_updated_at" in table_columns:
            update_pairs["scoring_updated_at"] = None  # placeholder, use NOW() in SQL

        # Build SET clause
        set_parts = []
        values = []
        for col, val in update_pairs.items():
            if col == "scoring_updated_at":
                set_parts.append(f"{col} = NOW()")
            else:
                set_parts.append(f"{col} = %s")
                values.append(val)

        values.append(publication_id)

        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE publications SET {', '.join(set_parts)} WHERE {pk_col} = %s",
            values,
        )
        updated = cursor.rowcount > 0
        conn.commit()

        if updated:
            logger.debug(
                "Updated publication scoring: pub_id=%s, score=%s (cols=%d)",
                publication_id[:16],
                final_relevancy_score,
                len(update_pairs),
            )
        else:
            logger.debug(
                "No publication row found to update: pub_id=%s",
                publication_id[:16],
            )

        return {"success": True, "updated": updated, "error": None}

    except Exception as e:
        logger.warning("Failed to update publication scoring: %s", e)
        if conn:
            conn.rollback()
        return {"success": False, "updated": False, "error": str(e)}
    finally:
        if conn:
            cursor.close()
            _put_connection(conn)


def update_publication_canonical_url(
    publication_id: str,
    canonical_url: Optional[str],
    doi: Optional[str] = None,
    pmid: Optional[str] = None,
    source_type: Optional[str] = None,
    database_url: str = None,
) -> dict:
    """Update a publication's canonical URL and related fields.

    Args:
        publication_id: Publication ID
        canonical_url: Canonical URL to set
        doi: DOI to set (optional)
        pmid: PMID to set (optional)
        source_type: Source type to set (optional)
        database_url: PostgreSQL connection URL

    Returns:
        Dictionary with update result
    """
    conn = None
    try:
        conn = _get_connection(database_url)
        cursor = conn.cursor()

        # Build dynamic UPDATE statement
        fields = []
        values = []

        if canonical_url is not None:
            fields.append("canonical_url = %s")
            values.append(canonical_url)

        if doi is not None:
            fields.append("doi = %s")
            values.append(doi)

        if pmid is not None:
            fields.append("pmid = %s")
            values.append(pmid)

        if source_type is not None:
            fields.append("source_type = %s")
            values.append(source_type)

        if not fields:
            return {"success": True, "updated": False, "error": None}

        values.append(publication_id)

        # Use dynamically-detected PK column instead of hardcoding
        # "publication_id" (the table may use "id" or "pub_id" instead).
        pk_col = _get_publications_table_metadata(conn, database_url)[1] or "publication_id"

        cursor.execute(
            f"UPDATE publications SET {', '.join(fields)} WHERE {pk_col} = %s",
            values
        )

        updated = cursor.rowcount > 0
        conn.commit()

        return {
            "success": True,
            "updated": updated,
            "error": None,
        }

    except Exception as e:
        logger.warning("Failed to update publication canonical URL: %s", e)
        if conn:
            conn.rollback()
        return {
            "success": False,
            "updated": False,
            "error": str(e),
        }
    finally:
        if conn:
            cursor.close()
            _put_connection(conn)


def get_publications_missing_canonical_url(
    since_days: Optional[int] = None,
    limit: Optional[int] = None,
    database_url: str = None,
) -> List[Dict]:
    """Get publications that don't have a canonical URL.

    Args:
        since_days: Only get publications from the last N days (optional)
        limit: Maximum number of publications to return (optional)
        database_url: PostgreSQL connection URL

    Returns:
        List of publication dictionaries
    """
    conn = None
    try:
        conn = _get_connection(database_url)
        cursor = conn.cursor()

        query = """
            SELECT publication_id, title, url, doi, pmid, source, source_type, published_date, raw_text
            FROM publications
            WHERE canonical_url IS NULL OR canonical_url = ''
        """
        params = []

        if since_days is not None:
            query += " AND created_at >= NOW() - INTERVAL '%s days'"
            params.append(since_days)

        query += " ORDER BY created_at DESC"

        if limit is not None:
            query += " LIMIT %s"
            params.append(limit)

        cursor.execute(query, params)

        results = []
        for row in cursor.fetchall():
            results.append({
                "id": row[0],
                "title": row[1],
                "url": row[2],
                "doi": row[3],
                "pmid": row[4],
                "source": row[5],
                "source_type": row[6],
                "published_date": row[7].isoformat() if row[7] else None,
                "raw_text": row[8],
            })

        return results

    except Exception as e:
        logger.warning("Failed to get publications missing canonical URL: %s", e)
        return []
    finally:
        if conn:
            cursor.close()
            _put_connection(conn)


def get_publication_by_id(
    publication_id: str,
    database_url: str = None,
) -> Optional[Dict]:
    """Get a single publication by ID.

    Args:
        publication_id: Publication ID
        database_url: PostgreSQL connection URL

    Returns:
        Publication dictionary or None
    """
    conn = None
    try:
        conn = _get_connection(database_url)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT publication_id, title, authors, source, venue, published_date, url, raw_text,
                   summary, doi, pmid, canonical_url, source_type
            FROM publications
            WHERE publication_id = %s
        """, (publication_id,))

        row = cursor.fetchone()

        if not row:
            return None

        return {
            "id": row[0],
            "title": row[1],
            "authors": row[2],
            "source": row[3],
            "venue": row[4],
            "published_date": row[5].isoformat() if row[5] else None,
            "url": row[6],
            "raw_text": row[7],
            "summary": row[8],
            "doi": row[9],
            "pmid": row[10],
            "canonical_url": row[11],
            "source_type": row[12],
        }

    except Exception as e:
        logger.warning("Failed to get publication by ID: %s", e)
        return None
    finally:
        if conn:
            cursor.close()
            _put_connection(conn)


def store_publication_embedding(
    publication_id: str,
    embedding_model: str,
    embedding_dim: int,
    embedding: bytes,
    content_hash: str,
    database_url: str = None,
) -> dict:
    """Store an embedding for a publication.

    Args:
        publication_id: Publication ID
        embedding_model: Name of the embedding model used
        embedding_dim: Dimension of the embedding vector
        embedding: Embedding bytes (numpy array as bytes)
        content_hash: SHA256 hash of the input text
        database_url: PostgreSQL connection URL

    Returns:
        Dictionary with storage result
    """
    conn = None
    try:
        conn = _get_connection(database_url)
        cursor = conn.cursor()

        # Use UPSERT with publication_id as the primary key
        # Store embedding as bytes in embedding_bytes column
        cursor.execute("""
            INSERT INTO publication_embeddings (
                publication_id, embedding_model, embedding_dim, embedding_bytes, content_hash,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (publication_id) DO UPDATE SET
                embedding_bytes = EXCLUDED.embedding_bytes,
                embedding_model = EXCLUDED.embedding_model,
                embedding_dim = EXCLUDED.embedding_dim,
                content_hash = EXCLUDED.content_hash,
                updated_at = NOW()
        """, (
            publication_id,
            embedding_model,
            embedding_dim,
            embedding,
            content_hash,
        ))

        conn.commit()

        logger.debug(
            "Stored embedding for publication %s (model=%s, dim=%d)",
            publication_id[:16],
            embedding_model,
            embedding_dim,
        )

        return {
            "success": True,
            "error": None,
        }

    except Exception as e:
        logger.warning("Failed to store embedding: %s", e)
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


def get_publication_embedding(
    publication_id: str,
    embedding_model: str,
    database_url: str = None,
) -> Optional[Dict]:
    """Get an embedding for a publication.

    Args:
        publication_id: Publication ID
        embedding_model: Name of the embedding model
        database_url: PostgreSQL connection URL

    Returns:
        Dictionary with embedding data or None
    """
    conn = None
    try:
        conn = _get_connection(database_url)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT embedding, embedding_dim, content_hash, created_at
            FROM publication_embeddings
            WHERE publication_id = %s AND embedding_model = %s
            ORDER BY created_at DESC
            LIMIT 1
        """, (publication_id, embedding_model))

        row = cursor.fetchone()

        if not row:
            return None

        return {
            "embedding": bytes(row[0]),
            "embedding_dim": row[1],
            "content_hash": row[2],
            "created_at": row[3].isoformat() if row[3] else None,
        }

    except Exception as e:
        logger.warning("Failed to get embedding: %s", e)
        return None
    finally:
        if conn:
            cursor.close()
            _put_connection(conn)


def get_publications_missing_embeddings(
    embedding_model: str,
    since_days: Optional[int] = None,
    limit: Optional[int] = None,
    database_url: str = None,
) -> List[Dict]:
    """Get publications that don't have an embedding for the given model.

    Args:
        embedding_model: Name of the embedding model
        since_days: Only get publications from the last N days (optional)
        limit: Maximum number of publications to return (optional)
        database_url: PostgreSQL connection URL

    Returns:
        List of publication dictionaries
    """
    conn = None
    try:
        conn = _get_connection(database_url)
        cursor = conn.cursor()

        query = """
            SELECT p.publication_id, p.title, p.raw_text, p.summary, p.source, p.venue, p.published_date
            FROM publications p
            LEFT JOIN publication_embeddings pe
                ON p.publication_id = pe.publication_id AND pe.embedding_model = %s
            WHERE pe.publication_id IS NULL
        """
        params = [embedding_model]

        if since_days is not None:
            query += " AND p.created_at >= NOW() - INTERVAL '%s days'"
            params.append(since_days)

        query += " ORDER BY p.created_at DESC"

        if limit is not None:
            query += " LIMIT %s"
            params.append(limit)

        cursor.execute(query, params)

        results = []
        for row in cursor.fetchall():
            results.append({
                "id": row[0],
                "title": row[1],
                "raw_text": row[2],
                "summary": row[3],
                "source": row[4],
                "venue": row[5],
                "published_date": row[6].isoformat() if row[6] else None,
            })

        return results

    except Exception as e:
        logger.warning("Failed to get publications missing embeddings: %s", e)
        return []
    finally:
        if conn:
            cursor.close()
            _put_connection(conn)


def get_all_embeddings_for_model(
    embedding_model: str,
    since_days: Optional[int] = None,
    database_url: str = None,
) -> List[Dict]:
    """Get all embeddings for a given model, optionally filtered by date.

    Args:
        embedding_model: Name of the embedding model
        since_days: Only get embeddings from the last N days (optional)
        database_url: PostgreSQL connection URL

    Returns:
        List of dictionaries with publication_id, embedding, and metadata
    """
    conn = None
    try:
        conn = _get_connection(database_url)
        cursor = conn.cursor()

        query = """
            SELECT pe.publication_id, pe.embedding_bytes, pe.embedding_dim, pe.content_hash,
                   p.title, p.source, p.published_date, p.canonical_url
            FROM publication_embeddings pe
            JOIN publications p ON pe.publication_id = p.publication_id
            WHERE pe.embedding_model = %s
        """
        params = [embedding_model]

        if since_days is not None:
            query += " AND p.created_at >= NOW() - INTERVAL '%s days'"
            params.append(since_days)

        query += " ORDER BY p.created_at DESC"

        cursor.execute(query, params)

        results = []
        for row in cursor.fetchall():
            results.append({
                "publication_id": row[0],
                "embedding": bytes(row[1]),
                "embedding_dim": row[2],
                "content_hash": row[3],
                "title": row[4],
                "source": row[5],
                "published_date": row[6].isoformat() if row[6] else None,
                "canonical_url": row[7],
            })

        return results

    except Exception as e:
        logger.warning("Failed to get embeddings for model: %s", e)
        return []
    finally:
        if conn:
            cursor.close()
            _put_connection(conn)


def get_all_publications(
    since_days: Optional[int] = None,
    limit: Optional[int] = None,
    database_url: str = None,
) -> List[Dict]:
    """Get all publications, optionally filtered by date.

    Args:
        since_days: Only get publications from the last N days (optional)
        limit: Maximum number of publications to return (optional)
        database_url: PostgreSQL connection URL

    Returns:
        List of publication dictionaries
    """
    conn = None
    try:
        conn = _get_connection(database_url)
        cursor = conn.cursor()

        query = """
            SELECT publication_id, title, authors, source, venue, published_date, url, raw_text,
                   summary, doi, pmid, canonical_url, source_type, created_at
            FROM publications
            WHERE 1=1
        """
        params = []

        if since_days is not None:
            query += " AND created_at >= NOW() - INTERVAL '%s days'"
            params.append(since_days)

        query += " ORDER BY created_at DESC"

        if limit is not None:
            query += " LIMIT %s"
            params.append(limit)

        cursor.execute(query, params)

        results = []
        for row in cursor.fetchall():
            results.append({
                "id": row[0],
                "title": row[1],
                "authors": row[2],
                "source": row[3],
                "venue": row[4],
                "published_date": row[5].isoformat() if row[5] else None,
                "url": row[6],
                "raw_text": row[7],
                "summary": row[8],
                "doi": row[9],
                "pmid": row[10],
                "canonical_url": row[11],
                "source_type": row[12],
                "created_at": row[13].isoformat() if row[13] else None,
            })

        return results

    except Exception as e:
        logger.warning("Failed to get all publications: %s", e)
        return []
    finally:
        if conn:
            cursor.close()
            _put_connection(conn)
