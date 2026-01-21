"""SQLite storage for acitrack publications.

This module provides persistent storage for all fetched publications,
enabling future trend analysis and historical queries.

The database is additive-only and does not affect existing pipeline behavior.
If the database fails, the pipeline continues normally with a warning.
"""

import logging
import sqlite3
from pathlib import Path
from typing import List

from acitrack_types import Publication

logger = logging.getLogger(__name__)

# Database file location
DEFAULT_DB_PATH = "data/db/acitrack.db"

# Schema version for future migrations
SCHEMA_VERSION = 6  # Bumped for relevancy_scoring_events table


def _init_schema(conn: sqlite3.Connection) -> None:
    """Initialize the database schema.

    Args:
        conn: SQLite database connection
    """
    cursor = conn.cursor()

    # Publications table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS publications (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            authors TEXT,
            source TEXT NOT NULL,
            venue TEXT,
            published_date TEXT,
            url TEXT,
            raw_text TEXT,
            summary TEXT,
            run_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source_names TEXT
        )
    """)

    # Indexes for common queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_publications_published_date
        ON publications(published_date)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_publications_source
        ON publications(source)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_publications_run_id
        ON publications(run_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_publications_created_at
        ON publications(created_at)
    """)

    # Schema version tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Get current schema version
    cursor.execute("SELECT MAX(version) FROM schema_version")
    row = cursor.fetchone()
    current_version = row[0] if row[0] is not None else 0

    # Apply migrations if needed
    if current_version < 2:
        logger.info("Migrating schema from version %d to 2", current_version)
        _migrate_to_v2(cursor)
        cursor.execute("INSERT INTO schema_version (version) VALUES (2)")
        current_version = 2

    if current_version < 3:
        logger.info("Migrating schema from version %d to 3", current_version)
        _migrate_to_v3(cursor)
        cursor.execute("INSERT INTO schema_version (version) VALUES (3)")
        current_version = 3

    if current_version < 4:
        logger.info("Migrating schema from version %d to 4", current_version)
        _migrate_to_v4(cursor)
        cursor.execute("INSERT INTO schema_version (version) VALUES (4)")
        current_version = 4

    if current_version < 5:
        logger.info("Migrating schema from version %d to 5", current_version)
        _migrate_to_v5(cursor)
        cursor.execute("INSERT INTO schema_version (version) VALUES (5)")
        current_version = 5

    if current_version < 6:
        logger.info("Migrating schema from version %d to 6", current_version)
        _migrate_to_v6(cursor)
        cursor.execute("INSERT INTO schema_version (version) VALUES (6)")
        current_version = 6

    conn.commit()
    logger.info("Database schema initialized (version %d)", current_version)


def _migrate_to_v2(cursor: sqlite3.Cursor) -> None:
    """Migrate database schema to version 2.

    Adds run history tracking tables.

    Args:
        cursor: Database cursor
    """
    # Runs table for run metadata
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            since_timestamp TEXT,
            max_items_per_source INTEGER,
            sources_count INTEGER,
            total_fetched INTEGER,
            total_deduped INTEGER,
            new_count INTEGER,
            unchanged_count INTEGER,
            summarized_count INTEGER,
            upload_drive INTEGER DEFAULT 0
        )
    """)

    # Pub_runs table for per-run publication tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pub_runs (
            run_id TEXT NOT NULL,
            pub_id TEXT NOT NULL,
            status TEXT NOT NULL,
            source TEXT,
            published_date TEXT,
            PRIMARY KEY (run_id, pub_id)
        )
    """)

    # Indexes for pub_runs queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pub_runs_run_id
        ON pub_runs(run_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pub_runs_status
        ON pub_runs(status)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pub_runs_source
        ON pub_runs(source)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pub_runs_published_date
        ON pub_runs(published_date)
    """)

    logger.info("Schema migrated to version 2: added runs and pub_runs tables")


def _migrate_to_v3(cursor: sqlite3.Cursor) -> None:
    """Migrate database schema to version 3.

    Adds must-reads rerank cache table.

    Args:
        cursor: Database cursor
    """
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS must_reads_rerank_cache (
            pub_id TEXT NOT NULL,
            rerank_version TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            model TEXT,
            llm_score REAL,
            llm_rank INTEGER,
            llm_reason TEXT,
            llm_why TEXT,
            llm_findings TEXT,
            PRIMARY KEY (pub_id, rerank_version)
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_rerank_cache_created_at
        ON must_reads_rerank_cache(created_at)
    """)

    logger.info("Schema migrated to version 3: added must_reads_rerank_cache table")


def _migrate_to_v4(cursor: sqlite3.Cursor) -> None:
    """Migrate database schema to version 4.

    Adds must-reads summary cache table for LLM-generated summaries.

    Args:
        cursor: Database cursor
    """
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS must_reads_summary_cache (
            pub_id TEXT NOT NULL,
            summary_version TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            model TEXT,
            why_it_matters TEXT,
            key_findings TEXT,
            study_type TEXT,
            evidence_strength TEXT,
            evidence_rationale TEXT,
            PRIMARY KEY (pub_id, summary_version)
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_summary_cache_created_at
        ON must_reads_summary_cache(created_at)
    """)

    logger.info("Schema migrated to version 4: added must_reads_summary_cache table")


def _migrate_to_v5(cursor: sqlite3.Cursor) -> None:
    """Migrate database schema to version 5.

    Adds expansion and scoring fields to publications.

    Args:
        cursor: Database cursor
    """
    new_columns = [
        ("doi", "TEXT"),
        ("relevance_score", "INTEGER DEFAULT 0"),
        ("credibility_score", "INTEGER DEFAULT 0"),
        ("main_interesting_fact", "TEXT"),
        ("relevance_to_spotitearly", "TEXT"),
        ("modality_tags", "TEXT"),  # JSON array
        ("sample_size", "INTEGER"),
        ("study_type", "TEXT"),
        ("key_metrics", "TEXT"),  # JSON dict
        ("sponsor_flag", "INTEGER DEFAULT 0"),
    ]

    for col_name, col_type in new_columns:
        try:
            cursor.execute(f"ALTER TABLE publications ADD COLUMN {col_name} {col_type}")
            logger.info("Added column: %s", col_name)
        except sqlite3.OperationalError as e:
            # Column might already exist (or older SQLite limitations)
            logger.debug("Column %s: %s", col_name, e)

    logger.info("Schema migrated to version 5: added expansion and scoring fields")


def _migrate_to_v6(cursor: sqlite3.Cursor) -> None:
    """Migrate database schema to version 6.

    Adds relevancy_scoring_events table for tracking LLM-based relevancy scoring.

    Args:
        cursor: Database cursor
    """
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS relevancy_scoring_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            publication_id TEXT NOT NULL,
            source TEXT,
            prompt_version TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            relevancy_score INTEGER,
            relevancy_reason TEXT,
            confidence TEXT,
            signals_json TEXT,
            input_fingerprint TEXT,
            raw_response_json TEXT,
            latency_ms INTEGER,
            cost_usd REAL,
            UNIQUE(run_id, publication_id, prompt_version)
        )
    """)

    # Indexes for common queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_relevancy_events_run_id
        ON relevancy_scoring_events(run_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_relevancy_events_pub_id
        ON relevancy_scoring_events(publication_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_relevancy_events_created_at
        ON relevancy_scoring_events(created_at)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_relevancy_events_mode
        ON relevancy_scoring_events(mode)
    """)

    logger.info("Schema migrated to version 6: added relevancy_scoring_events table")


def _get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Get a connection to the SQLite database.

    Creates the database and schema if they don't exist.

    Args:
        db_path: Path to the database file

    Returns:
        SQLite database connection

    Raises:
        sqlite3.Error: If connection fails
    """
    # Ensure database directory exists
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    # Connect to database
    conn = sqlite3.connect(db_path)

    # Initialize schema if needed
    _init_schema(conn)

    return conn


def store_publications(
    publications: List[Publication],
    run_id: str,
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    """Store publications in the SQLite database.

    This function is idempotent - duplicate publications (same ID) are ignored.
    If the database operation fails, the function logs a warning and returns
    error information without raising an exception.

    Args:
        publications: List of Publication objects to store
        run_id: Run identifier for this batch
        db_path: Path to database file (default: data/db/acitrack.db)

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

    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()

        inserted = 0
        duplicates = 0

        for pub in publications:
            try:
                authors_str = ", ".join(pub.authors) if pub.authors else ""
                source_names_str = ", ".join(pub.source_names) if getattr(pub, "source_names", None) else ""

                cursor.execute("""
                    INSERT OR IGNORE INTO publications (
                        id, title, authors, source, venue, published_date,
                        url, raw_text, summary, run_id, source_names
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

            except sqlite3.Error as e:
                logger.warning("Failed to insert publication %s: %s", getattr(pub, "id", "UNKNOWN"), e)
                duplicates += 1
                continue

        conn.commit()
        conn.close()

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
        logger.warning("Failed to store publications to database: %s", e)
        return {
            "success": False,
            "total": len(publications),
            "inserted": 0,
            "duplicates": 0,
            "error": str(e),
        }


def get_publication_count(db_path: str = DEFAULT_DB_PATH) -> int:
    """Get total number of publications in the database.

    Args:
        db_path: Path to database file

    Returns:
        Total count of publications, or -1 if query fails
    """
    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM publications")
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        logger.warning("Failed to get publication count: %s", e)
        return -1


def get_source_stats(db_path: str = DEFAULT_DB_PATH) -> List[dict]:
    """Get publication counts by source.

    Args:
        db_path: Path to database file

    Returns:
        List of dicts with 'source' and 'count' keys, or empty list if query fails
    """
    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT source, COUNT(*) as count
            FROM publications
            GROUP BY source
            ORDER BY count DESC
        """)
        results = [{"source": row[0], "count": row[1]} for row in cursor.fetchall()]
        conn.close()
        return results
    except Exception as e:
        logger.warning("Failed to get source stats: %s", e)
        return []


def store_run_history(
    run_id: str,
    started_at: str,
    since_timestamp: str,
    max_items_per_source: int,
    sources_count: int,
    total_fetched: int,
    total_deduped: int,
    new_count: int,
    unchanged_count: int,
    summarized_count: int,
    upload_drive: bool,
    publications_with_status: list,
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    """Store run history and per-run publication tracking.

    This function stores metadata about a pipeline run and tracks which
    publications were seen in that run with their status (new/unchanged).

    Args:
        run_id: Unique run identifier
        started_at: Run start timestamp (ISO format)
        since_timestamp: Since timestamp for fetching (ISO format)
        max_items_per_source: Max items per source setting
        sources_count: Number of sources processed
        total_fetched: Total publications fetched (before dedup)
        total_deduped: Total publications after deduplication
        new_count: Count of new publications
        unchanged_count: Count of unchanged publications
        summarized_count: Count of publications summarized
        upload_drive: Whether Drive upload was enabled
        publications_with_status: List of dicts with 'id', 'status', 'source', 'date'
        db_path: Path to database file

    Returns:
        Dictionary with storage statistics
    """
    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()

        # Insert run metadata
        cursor.execute("""
            INSERT INTO runs (
                run_id, started_at, since_timestamp, max_items_per_source,
                sources_count, total_fetched, total_deduped, new_count,
                unchanged_count, summarized_count, upload_drive
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            1 if upload_drive else 0,
        ))

        # Insert pub_runs entries
        pub_runs_inserted = 0
        for pub in publications_with_status:
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO pub_runs (
                        run_id, pub_id, status, source, published_date
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    run_id,
                    pub.get("id", ""),
                    pub.get("status", ""),
                    pub.get("source", ""),
                    pub.get("date", ""),
                ))
                if cursor.rowcount > 0:
                    pub_runs_inserted += 1
            except sqlite3.Error as e:
                logger.debug("Failed to insert pub_run for %s: %s", pub.get("id", ""), e)
                continue

        conn.commit()
        conn.close()

        logger.info(
            "Stored run history: run_id=%s, new=%d, unchanged=%d, pub_runs=%d",
            run_id,
            new_count,
            unchanged_count,
            pub_runs_inserted,
        )

        return {
            "success": True,
            "run_id": run_id,
            "pub_runs_inserted": pub_runs_inserted,
            "error": None,
        }

    except Exception as e:
        logger.warning("Failed to store run history: %s", e)
        return {
            "success": False,
            "run_id": run_id,
            "pub_runs_inserted": 0,
            "error": str(e),
        }


def get_run_history(limit: int = 10, db_path: str = DEFAULT_DB_PATH) -> List[dict]:
    """Get recent run history.

    Args:
        limit: Maximum number of runs to return
        db_path: Path to database file

    Returns:
        List of run dictionaries, or empty list if query fails
    """
    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                run_id, started_at, since_timestamp, max_items_per_source,
                sources_count, total_fetched, total_deduped, new_count,
                unchanged_count, summarized_count, upload_drive
            FROM runs
            ORDER BY started_at DESC
            LIMIT ?
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
                "upload_drive": row[10] == 1,
            })

        conn.close()
        return results

    except Exception as e:
        logger.warning("Failed to get run history: %s", e)
        return []


def store_relevancy_scoring_event(
    run_id: str,
    mode: str,
    publication_id: str,
    source: str,
    prompt_version: str,
    model: str,
    relevancy_score: Optional[int],
    relevancy_reason: str,
    confidence: str,
    signals: dict,
    input_fingerprint: Optional[str] = None,
    raw_response: Optional[dict] = None,
    latency_ms: Optional[int] = None,
    cost_usd: Optional[float] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    """Store a relevancy scoring event to the database.

    This function uses INSERT OR REPLACE to handle idempotency based on
    the unique constraint (run_id, publication_id, prompt_version).

    Args:
        run_id: Run identifier (e.g., "daily-2026-01-20")
        mode: Run mode ("daily" or "weekly")
        publication_id: Publication identifier
        source: Publication source
        prompt_version: Scoring prompt version (e.g., "poc_v2")
        model: LLM model used (e.g., "gpt-4o-mini")
        relevancy_score: Score 0-100 or None if scoring failed
        relevancy_reason: Explanation text
        confidence: "low", "medium", or "high"
        signals: Dict with structured signals (cancer_type, breath_based, etc.)
        input_fingerprint: Optional hash of title+abstract for deduplication
        raw_response: Optional dict of raw LLM response for debugging
        latency_ms: Optional latency in milliseconds
        cost_usd: Optional cost in USD
        db_path: Path to database file

    Returns:
        Dictionary with storage result:
        {
            "success": bool,
            "event_id": int or None,
            "error": str or None
        }
    """
    import json

    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()

        # Convert signals dict to JSON string
        signals_json = json.dumps(signals) if signals else None

        # Convert raw_response dict to JSON string
        raw_response_json = json.dumps(raw_response) if raw_response else None

        cursor.execute("""
            INSERT OR REPLACE INTO relevancy_scoring_events (
                run_id, mode, publication_id, source, prompt_version, model,
                relevancy_score, relevancy_reason, confidence, signals_json,
                input_fingerprint, raw_response_json, latency_ms, cost_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

        event_id = cursor.lastrowid
        conn.commit()
        conn.close()

        logger.debug("Stored relevancy scoring event: run_id=%s, pub_id=%s, score=%s",
                    run_id, publication_id, relevancy_score)

        return {
            "success": True,
            "event_id": event_id,
            "error": None,
        }

    except Exception as e:
        logger.warning("Failed to store relevancy scoring event: %s", e)
        return {
            "success": False,
            "event_id": None,
            "error": str(e),
        }


def get_relevancy_scores_for_run(
    run_id: str,
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    """Get all relevancy scoring events for a specific run.

    Args:
        run_id: Run identifier to query
        db_path: Path to database file

    Returns:
        Dictionary mapping publication_id to scoring result:
        {
            "pub_id_1": {
                "relevancy_score": 78,
                "relevancy_reason": "...",
                "confidence": "high",
                "signals": {...},
                "prompt_version": "poc_v2",
                "model": "gpt-4o-mini",
                "created_at": "2026-01-20T12:00:00",
            },
            ...
        }
    """
    import json

    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                publication_id, relevancy_score, relevancy_reason, confidence,
                signals_json, prompt_version, model, created_at
            FROM relevancy_scoring_events
            WHERE run_id = ?
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

        conn.close()
        logger.debug("Retrieved %d relevancy scores for run_id=%s", len(results), run_id)
        return results

    except Exception as e:
        logger.warning("Failed to get relevancy scores for run %s: %s", run_id, e)
        return {}


def export_relevancy_events_to_jsonl(
    run_id: str,
    output_path: str,
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    """Export relevancy scoring events for a run to JSONL file.

    Args:
        run_id: Run identifier to export
        output_path: Path to output JSONL file
        db_path: Path to database file

    Returns:
        Dictionary with export statistics:
        {
            "success": bool,
            "events_exported": int,
            "output_path": str,
            "error": str or None
        }
    """
    import json

    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                run_id, mode, publication_id, source, prompt_version, model,
                created_at, relevancy_score, relevancy_reason, confidence,
                signals_json, input_fingerprint, latency_ms, cost_usd
            FROM relevancy_scoring_events
            WHERE run_id = ?
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
                    "created_at": row[6],
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

        conn.close()

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