"""SQLite storage for acitrack publications.

This module provides persistent storage for all fetched publications,
enabling future trend analysis and historical queries.

The database is additive-only and does not affect existing pipeline behavior.
If the database fails, the pipeline continues normally with a warning.
"""

import logging
import sqlite3
from pathlib import Path
from typing import List, Optional, Dict

from acitrack_types import Publication

logger = logging.getLogger(__name__)

# Database file location
DEFAULT_DB_PATH = "data/db/acitrack.db"

# Schema version for future migrations
SCHEMA_VERSION = 8  # Bumped for canonical_url, source_type, pmid columns and publication_embeddings table


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

    if current_version < 7:
        logger.info("Migrating schema from version %d to 7", current_version)
        _migrate_to_v7(cursor)
        cursor.execute("INSERT INTO schema_version (version) VALUES (7)")
        current_version = 7

    if current_version < 8:
        logger.info("Migrating schema from version %d to 8", current_version)
        _migrate_to_v8(cursor)
        cursor.execute("INSERT INTO schema_version (version) VALUES (8)")
        current_version = 8

    # Ensure credibility columns exist (idempotent check run on every init)
    _ensure_tri_model_credibility_columns(cursor)

    # Ensure canonical URL and embedding columns/tables exist (idempotent)
    _ensure_canonical_url_columns(cursor)
    _ensure_publication_embeddings_table(cursor)

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
            input_fingerprint TEXT,
            response_json TEXT,
            PRIMARY KEY (pub_id, rerank_version)
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_must_reads_rerank_cache_created_at
        ON must_reads_rerank_cache(created_at)
    """)

    logger.info("Schema migrated to version 3: added must_reads_rerank_cache table")


def _migrate_to_v4(cursor: sqlite3.Cursor) -> None:
    """Migrate database schema to version 4.

    Adds bibliometric enrichment columns to publications table.

    Args:
        cursor: Database cursor
    """
    # Add new columns for bibliometric data
    new_columns = [
        ("doi", "TEXT"),
        ("citation_count", "INTEGER"),
        ("citations_per_year", "REAL"),
        ("venue_name", "TEXT"),
        ("pub_type", "TEXT"),
    ]

    for col_name, col_type in new_columns:
        try:
            cursor.execute(f"ALTER TABLE publications ADD COLUMN {col_name} {col_type}")
            logger.info("Added column '%s' to publications table", col_name)
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                logger.debug("Column '%s' already exists, skipping", col_name)
            else:
                raise

    logger.info("Schema migrated to version 4: added bibliometric columns")


def _migrate_to_v5(cursor: sqlite3.Cursor) -> None:
    """Migrate database schema to version 5.

    Adds enrichment columns to publications table.

    Args:
        cursor: Database cursor
    """
    # Add enrichment columns
    enrichment_columns = [
        ("relevance_score", "INTEGER"),
        ("credibility_score", "INTEGER"),
        ("main_interesting_fact", "TEXT"),
        ("relevance_to_spotitearly", "TEXT"),
        ("modality_tags", "TEXT"),
        ("sample_size", "TEXT"),
        ("study_type", "TEXT"),
        ("key_metrics", "TEXT"),
        ("sponsor_flag", "TEXT"),
    ]

    for col_name, col_type in enrichment_columns:
        try:
            cursor.execute(f"ALTER TABLE publications ADD COLUMN {col_name} {col_type}")
            logger.info("Added column '%s' to publications table", col_name)
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                logger.debug("Column '%s' already exists, skipping", col_name)
            else:
                raise

    logger.info("Schema migrated to version 5: added enrichment columns")


def _migrate_to_v6(cursor: sqlite3.Cursor) -> None:
    """Migrate database schema to version 6.

    Adds relevancy scoring events table.

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


def _migrate_to_v7(cursor: sqlite3.Cursor) -> None:
    """Migrate database schema to version 7.

    Adds tri-model scoring events table.

    Args:
        cursor: Database cursor
    """
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tri_model_scoring_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            publication_id TEXT NOT NULL,
            title TEXT,
            source TEXT,
            published_date TEXT,
            claude_review_json TEXT,
            gemini_review_json TEXT,
            gpt_eval_json TEXT,
            final_relevancy_score INTEGER,
            final_relevancy_reason TEXT,
            final_signals_json TEXT,
            final_summary TEXT,
            agreement_level TEXT,
            disagreements TEXT,
            evaluator_rationale TEXT,
            confidence TEXT,
            prompt_versions_json TEXT,
            model_names_json TEXT,
            claude_latency_ms INTEGER,
            gemini_latency_ms INTEGER,
            gpt_latency_ms INTEGER,
            credibility_score INTEGER,
            credibility_reason TEXT,
            credibility_confidence TEXT,
            credibility_signals_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(run_id, publication_id)
        )
    """)

    # Indexes for common queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tri_model_events_run_id
        ON tri_model_scoring_events(run_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tri_model_events_pub_id
        ON tri_model_scoring_events(publication_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tri_model_events_created_at
        ON tri_model_scoring_events(created_at)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tri_model_events_mode
        ON tri_model_scoring_events(mode)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tri_model_events_score
        ON tri_model_scoring_events(final_relevancy_score)
    """)

    logger.info("Schema migrated to version 7: added tri_model_scoring_events table")


def _migrate_to_v8(cursor: sqlite3.Cursor) -> None:
    """Migrate database schema to version 8.

    Adds canonical_url, source_type, pmid columns to publications table
    and creates publication_embeddings table for semantic search.

    Args:
        cursor: Database cursor
    """
    # Add new columns to publications table
    new_columns = [
        ("canonical_url", "TEXT"),
        ("source_type", "TEXT"),  # pubmed, rss, biorxiv, medrxiv, arxiv, etc.
        ("pmid", "TEXT"),
    ]

    for col_name, col_type in new_columns:
        try:
            cursor.execute(f"ALTER TABLE publications ADD COLUMN {col_name} {col_type}")
            logger.info("Added column '%s' to publications table", col_name)
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                logger.debug("Column '%s' already exists, skipping", col_name)
            else:
                raise

    # Create index on canonical_url
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_publications_canonical_url
        ON publications(canonical_url)
    """)

    # Create index on pmid
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_publications_pmid
        ON publications(pmid)
    """)

    # Create publication_embeddings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS publication_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            publication_id TEXT NOT NULL,
            embedding_model TEXT NOT NULL,
            embedding_dim INTEGER NOT NULL,
            embedding BLOB NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(publication_id, embedding_model, content_hash),
            FOREIGN KEY (publication_id) REFERENCES publications(id)
        )
    """)

    # Create indexes for publication_embeddings
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pub_embeddings_publication_id
        ON publication_embeddings(publication_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pub_embeddings_content_hash
        ON publication_embeddings(content_hash)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pub_embeddings_model
        ON publication_embeddings(embedding_model)
    """)

    logger.info("Schema migrated to version 8: added canonical_url, source_type, pmid columns and publication_embeddings table")


def _ensure_canonical_url_columns(cursor: sqlite3.Cursor) -> None:
    """Ensure publications table has canonical_url, source_type, and pmid columns.

    This is called on every initialization to handle cases where the columns
    were not added during migration.

    Args:
        cursor: Database cursor
    """
    try:
        cursor.execute("PRAGMA table_info(publications)")
        columns = [row[1] for row in cursor.fetchall()]

        # Add missing columns
        canonical_columns = [
            ("canonical_url", "TEXT"),
            ("source_type", "TEXT"),
            ("pmid", "TEXT"),
        ]

        for col_name, col_type in canonical_columns:
            if col_name not in columns:
                cursor.execute(f"ALTER TABLE publications ADD COLUMN {col_name} {col_type}")
                logger.info("Added %s column to publications", col_name)

        # Ensure indexes exist
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_publications_canonical_url
            ON publications(canonical_url)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_publications_pmid
            ON publications(pmid)
        """)

    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            logger.debug("publications table doesn't exist yet, will be created by migration")
        else:
            logger.warning("Error ensuring canonical URL columns: %s", e)
    except Exception as e:
        logger.warning("Unexpected error ensuring canonical URL columns: %s", e)


def _ensure_publication_embeddings_table(cursor: sqlite3.Cursor) -> None:
    """Ensure publication_embeddings table exists with proper schema.

    This is called on every initialization to handle cases where the table
    was not created during migration.

    Args:
        cursor: Database cursor
    """
    try:
        # Create table if not exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS publication_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                publication_id TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                embedding_dim INTEGER NOT NULL,
                embedding BLOB NOT NULL,
                content_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(publication_id, embedding_model, content_hash),
                FOREIGN KEY (publication_id) REFERENCES publications(id)
            )
        """)

        # Ensure indexes exist
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_pub_embeddings_publication_id
            ON publication_embeddings(publication_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_pub_embeddings_content_hash
            ON publication_embeddings(content_hash)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_pub_embeddings_model
            ON publication_embeddings(embedding_model)
        """)

    except Exception as e:
        logger.warning("Error ensuring publication_embeddings table: %s", e)


def _ensure_tri_model_credibility_columns(cursor: sqlite3.Cursor) -> None:
    """Ensure tri_model_scoring_events table has credibility columns.

    This is called on every initialization to handle cases where the table
    was created without credibility columns (e.g., partial deployments).

    Args:
        cursor: Database cursor
    """
    try:
        cursor.execute("PRAGMA table_info(tri_model_scoring_events)")
        columns = [row[1] for row in cursor.fetchall()]

        # Add missing credibility columns
        credibility_columns = [
            ("credibility_score", "INTEGER"),
            ("credibility_reason", "TEXT"),
            ("credibility_confidence", "TEXT"),
            ("credibility_signals_json", "TEXT"),
        ]

        for col_name, col_type in credibility_columns:
            if col_name not in columns:
                cursor.execute(f"ALTER TABLE tri_model_scoring_events ADD COLUMN {col_name} {col_type}")
                logger.info("Added %s column to tri_model_scoring_events", col_name)
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            # Table doesn't exist yet, will be created by migration
            logger.debug("tri_model_scoring_events table doesn't exist yet, will be created by migration")
        else:
            logger.warning("Error ensuring tri_model credibility columns: %s", e)
    except Exception as e:
        logger.warning("Unexpected error ensuring tri_model credibility columns: %s", e)


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
        logger.error("Failed to store publications: %s", e)
        return {
            "success": False,
            "total": len(publications) if publications else 0,
            "inserted": 0,
            "duplicates": 0,
            "error": str(e),
        }


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
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    """Store run history metadata.

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
        db_path: Path to database file

    Returns:
        Dictionary with storage result:
        {
            "success": bool,
            "error": str or None
        }
    """
    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO runs (
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

        conn.commit()
        conn.close()

        logger.info("Stored run history for run_id=%s", run_id)

        return {
            "success": True,
            "error": None,
        }

    except Exception as e:
        logger.warning("Failed to store run history: %s (non-blocking)", e)
        return {
            "success": False,
            "error": str(e),
        }


def get_run_history(limit: int = 10, db_path: str = DEFAULT_DB_PATH) -> list:
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
    relevancy_reason: Optional[str],
    confidence: Optional[str],
    signals: Optional[Dict],
    input_fingerprint: Optional[str],
    raw_response: Optional[Dict],
    latency_ms: Optional[int],
    cost_usd: Optional[float],
    db_path: str = DEFAULT_DB_PATH,
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
        db_path: Path to database file

    Returns:
        Dictionary with storage result
    """
    import json

    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()

        signals_json = json.dumps(signals) if signals else None
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

        conn.commit()
        conn.close()

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
        return {
            "success": False,
            "error": str(e),
        }


def get_relevancy_scores_for_run(
    run_id: str,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Dict]:
    """Get all relevancy scores for a run.

    Args:
        run_id: Run identifier
        db_path: Path to database file

    Returns:
        Dictionary mapping publication_id to score data
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
    db_path: str = DEFAULT_DB_PATH,
    database_url: Optional[str] = None,  # For backwards compatibility
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
        disagreements: Disagreements text or list/dict (will be JSON-encoded)
        evaluator_rationale: Evaluator rationale
        confidence: Confidence level
        prompt_versions: Prompt versions dict
        model_names: Model names dict
        claude_latency_ms: Claude latency
        gemini_latency_ms: Gemini latency
        gpt_latency_ms: GPT latency
        credibility_score: Credibility score (0-100) or None
        credibility_reason: Credibility reason text or None
        credibility_confidence: Credibility confidence (low/medium/high) or None
        credibility_signals: Credibility signals dict or None
        db_path: Path to database file
        database_url: Database URL (for backwards compatibility, ignored)

    Returns:
        Dictionary with storage result
    """
    import json

    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()

        # Normalize disagreements to string
        # Handle case where it might be a list or dict from evaluator
        if isinstance(disagreements, (list, dict)):
            disagreements_str = json.dumps(disagreements, ensure_ascii=False)
        elif disagreements is None:
            disagreements_str = None
        else:
            disagreements_str = str(disagreements)

        # Ensure agreement_level is a string
        agreement_level_str = str(agreement_level) if agreement_level is not None else "unknown"

        cursor.execute("""
            INSERT OR REPLACE INTO tri_model_scoring_events (
                run_id, mode, publication_id, title, source, published_date,
                claude_review_json, gemini_review_json, gpt_eval_json,
                final_relevancy_score, final_relevancy_reason, final_signals_json,
                final_summary, agreement_level, disagreements, evaluator_rationale,
                confidence, prompt_versions_json, model_names_json,
                claude_latency_ms, gemini_latency_ms, gpt_latency_ms,
                credibility_score, credibility_reason, credibility_confidence, credibility_signals_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_id,
            mode,
            publication_id,
            title,
            source,
            published_date,
            json.dumps(claude_review, ensure_ascii=False) if claude_review else None,
            json.dumps(gemini_review, ensure_ascii=False) if gemini_review else None,
            json.dumps(gpt_eval, ensure_ascii=False) if gpt_eval else None,
            final_relevancy_score,
            final_relevancy_reason,
            json.dumps(final_signals, ensure_ascii=False),
            final_summary,
            agreement_level_str,
            disagreements_str,
            evaluator_rationale,
            confidence,
            json.dumps(prompt_versions, ensure_ascii=False),
            json.dumps(model_names, ensure_ascii=False),
            claude_latency_ms,
            gemini_latency_ms,
            gpt_latency_ms,
            credibility_score,
            credibility_reason,
            credibility_confidence,
            json.dumps(credibility_signals, ensure_ascii=False) if credibility_signals else None,
        ))

        conn.commit()
        conn.close()

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
        return {
            "success": False,
            "error": str(e),
        }


def export_tri_model_events_to_jsonl(
    run_id: str,
    output_path: str,
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    """Export tri-model scoring events for a run to JSONL file.

    Args:
        run_id: Run identifier to export
        output_path: Path to output JSONL file
        db_path: Path to database file

    Returns:
        Dictionary with export statistics
    """
    import json

    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                run_id, mode, publication_id, title, source, published_date,
                claude_review_json, gemini_review_json, gpt_eval_json,
                final_relevancy_score, final_relevancy_reason, final_signals_json,
                final_summary, agreement_level, disagreements, evaluator_rationale,
                confidence, prompt_versions_json, model_names_json,
                claude_latency_ms, gemini_latency_ms, gpt_latency_ms,
                credibility_score, credibility_reason, credibility_confidence, credibility_signals_json,
                created_at
            FROM tri_model_scoring_events
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
                    "credibility_score": row[22],
                    "credibility_reason": row[23],
                    "credibility_confidence": row[24],
                    "credibility_signals": json.loads(row[25]) if row[25] else {},
                    "created_at": row[26],
                }
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
                events_exported += 1

        conn.close()

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


def update_publication_canonical_url(
    publication_id: str,
    canonical_url: Optional[str],
    doi: Optional[str] = None,
    pmid: Optional[str] = None,
    source_type: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    """Update a publication's canonical URL and related fields.

    Args:
        publication_id: Publication ID
        canonical_url: Canonical URL to set
        doi: DOI to set (optional)
        pmid: PMID to set (optional)
        source_type: Source type to set (optional)
        db_path: Path to database file

    Returns:
        Dictionary with update result
    """
    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()

        # Build dynamic UPDATE statement
        fields = []
        values = []

        if canonical_url is not None:
            fields.append("canonical_url = ?")
            values.append(canonical_url)

        if doi is not None:
            fields.append("doi = ?")
            values.append(doi)

        if pmid is not None:
            fields.append("pmid = ?")
            values.append(pmid)

        if source_type is not None:
            fields.append("source_type = ?")
            values.append(source_type)

        if not fields:
            return {"success": True, "updated": False, "error": None}

        values.append(publication_id)

        cursor.execute(
            f"UPDATE publications SET {', '.join(fields)} WHERE id = ?",
            values
        )

        updated = cursor.rowcount > 0
        conn.commit()
        conn.close()

        return {
            "success": True,
            "updated": updated,
            "error": None,
        }

    except Exception as e:
        logger.warning("Failed to update publication canonical URL: %s", e)
        return {
            "success": False,
            "updated": False,
            "error": str(e),
        }


def get_publications_missing_canonical_url(
    since_days: Optional[int] = None,
    limit: Optional[int] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict]:
    """Get publications that don't have a canonical URL.

    Args:
        since_days: Only get publications from the last N days (optional)
        limit: Maximum number of publications to return (optional)
        db_path: Path to database file

    Returns:
        List of publication dictionaries
    """
    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()

        query = """
            SELECT id, title, url, doi, pmid, source, source_type, published_date, raw_text
            FROM publications
            WHERE canonical_url IS NULL OR canonical_url = ''
        """
        params = []

        if since_days is not None:
            query += " AND created_at >= datetime('now', ?)"
            params.append(f'-{since_days} days')

        query += " ORDER BY created_at DESC"

        if limit is not None:
            query += " LIMIT ?"
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
                "published_date": row[7],
                "raw_text": row[8],
            })

        conn.close()
        return results

    except Exception as e:
        logger.warning("Failed to get publications missing canonical URL: %s", e)
        return []


def get_publication_by_id(
    publication_id: str,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[Dict]:
    """Get a single publication by ID.

    Args:
        publication_id: Publication ID
        db_path: Path to database file

    Returns:
        Publication dictionary or None
    """
    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, title, authors, source, venue, published_date, url, raw_text,
                   summary, doi, pmid, canonical_url, source_type
            FROM publications
            WHERE id = ?
        """, (publication_id,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return {
            "id": row[0],
            "title": row[1],
            "authors": row[2],
            "source": row[3],
            "venue": row[4],
            "published_date": row[5],
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


def store_publication_embedding(
    publication_id: str,
    embedding_model: str,
    embedding_dim: int,
    embedding: bytes,
    content_hash: str,
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    """Store an embedding for a publication.

    Args:
        publication_id: Publication ID
        embedding_model: Name of the embedding model used
        embedding_dim: Dimension of the embedding vector
        embedding: Embedding bytes (numpy array as bytes)
        content_hash: SHA256 hash of the input text
        db_path: Path to database file

    Returns:
        Dictionary with storage result
    """
    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO publication_embeddings (
                publication_id, embedding_model, embedding_dim, embedding, content_hash
            ) VALUES (?, ?, ?, ?, ?)
        """, (
            publication_id,
            embedding_model,
            embedding_dim,
            embedding,
            content_hash,
        ))

        conn.commit()
        conn.close()

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
        return {
            "success": False,
            "error": str(e),
        }


def get_publication_embedding(
    publication_id: str,
    embedding_model: str,
    db_path: str = DEFAULT_DB_PATH,
) -> Optional[Dict]:
    """Get an embedding for a publication.

    Args:
        publication_id: Publication ID
        embedding_model: Name of the embedding model
        db_path: Path to database file

    Returns:
        Dictionary with embedding data or None
    """
    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT embedding, embedding_dim, content_hash, created_at
            FROM publication_embeddings
            WHERE publication_id = ? AND embedding_model = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (publication_id, embedding_model))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return {
            "embedding": row[0],
            "embedding_dim": row[1],
            "content_hash": row[2],
            "created_at": row[3],
        }

    except Exception as e:
        logger.warning("Failed to get embedding: %s", e)
        return None


def get_publications_missing_embeddings(
    embedding_model: str,
    since_days: Optional[int] = None,
    limit: Optional[int] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict]:
    """Get publications that don't have an embedding for the given model.

    Args:
        embedding_model: Name of the embedding model
        since_days: Only get publications from the last N days (optional)
        limit: Maximum number of publications to return (optional)
        db_path: Path to database file

    Returns:
        List of publication dictionaries
    """
    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()

        query = """
            SELECT p.id, p.title, p.raw_text, p.summary, p.source, p.venue, p.published_date
            FROM publications p
            LEFT JOIN publication_embeddings pe
                ON p.id = pe.publication_id AND pe.embedding_model = ?
            WHERE pe.id IS NULL
        """
        params = [embedding_model]

        if since_days is not None:
            query += " AND p.created_at >= datetime('now', ?)"
            params.append(f'-{since_days} days')

        query += " ORDER BY p.created_at DESC"

        if limit is not None:
            query += " LIMIT ?"
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
                "published_date": row[6],
            })

        conn.close()
        return results

    except Exception as e:
        logger.warning("Failed to get publications missing embeddings: %s", e)
        return []


def get_all_embeddings_for_model(
    embedding_model: str,
    since_days: Optional[int] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict]:
    """Get all embeddings for a given model, optionally filtered by date.

    Args:
        embedding_model: Name of the embedding model
        since_days: Only get embeddings from the last N days (optional)
        db_path: Path to database file

    Returns:
        List of dictionaries with publication_id, embedding, and metadata
    """
    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()

        query = """
            SELECT pe.publication_id, pe.embedding, pe.embedding_dim, pe.content_hash,
                   p.title, p.source, p.published_date, p.canonical_url
            FROM publication_embeddings pe
            JOIN publications p ON pe.publication_id = p.id
            WHERE pe.embedding_model = ?
        """
        params = [embedding_model]

        if since_days is not None:
            query += " AND p.created_at >= datetime('now', ?)"
            params.append(f'-{since_days} days')

        query += " ORDER BY p.created_at DESC"

        cursor.execute(query, params)

        results = []
        for row in cursor.fetchall():
            results.append({
                "publication_id": row[0],
                "embedding": row[1],
                "embedding_dim": row[2],
                "content_hash": row[3],
                "title": row[4],
                "source": row[5],
                "published_date": row[6],
                "canonical_url": row[7],
            })

        conn.close()
        return results

    except Exception as e:
        logger.warning("Failed to get embeddings for model: %s", e)
        return []


def get_all_publications(
    since_days: Optional[int] = None,
    limit: Optional[int] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict]:
    """Get all publications, optionally filtered by date.

    Args:
        since_days: Only get publications from the last N days (optional)
        limit: Maximum number of publications to return (optional)
        db_path: Path to database file

    Returns:
        List of publication dictionaries
    """
    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()

        query = """
            SELECT id, title, authors, source, venue, published_date, url, raw_text,
                   summary, doi, pmid, canonical_url, source_type, created_at
            FROM publications
            WHERE 1=1
        """
        params = []

        if since_days is not None:
            query += " AND created_at >= datetime('now', ?)"
            params.append(f'-{since_days} days')

        query += " ORDER BY created_at DESC"

        if limit is not None:
            query += " LIMIT ?"
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
                "published_date": row[5],
                "url": row[6],
                "raw_text": row[7],
                "summary": row[8],
                "doi": row[9],
                "pmid": row[10],
                "canonical_url": row[11],
                "source_type": row[12],
                "created_at": row[13],
            })

        conn.close()
        return results

    except Exception as e:
        logger.warning("Failed to get all publications: %s", e)
        return []
