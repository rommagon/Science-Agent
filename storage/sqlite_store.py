"""SQLite storage for acitrack publications.

This module provides persistent storage for all fetched publications,
enabling future trend analysis and historical queries.

The database is additive-only and does not affect existing pipeline behavior.
If the database fails, the pipeline continues normally with a warning.
"""

import logging
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List

from acitrack_types import Publication

logger = logging.getLogger(__name__)

# Database file location
DEFAULT_DB_PATH = "data/db/acitrack.db"

# Schema version for future migrations
SCHEMA_VERSION = 2  # Bumped for run history tables


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


def store_publications(publications: List[Publication], run_id: str, db_path: str = DEFAULT_DB_PATH) -> dict:
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
            "error": None
        }

    try:
        conn = _get_connection(db_path)
        cursor = conn.cursor()

        inserted = 0
        duplicates = 0

        for pub in publications:
            try:
                # Convert list fields to comma-separated strings for storage
                authors_str = ", ".join(pub.authors) if pub.authors else ""
                source_names_str = ", ".join(pub.source_names) if pub.source_names else ""

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
                    pub.venue,
                    pub.date,
                    pub.url,
                    pub.raw_text,
                    pub.summary,
                    run_id,
                    source_names_str
                ))

                if cursor.rowcount > 0:
                    inserted += 1
                else:
                    duplicates += 1

            except sqlite3.Error as e:
                logger.warning("Failed to insert publication %s: %s", pub.id, e)
                duplicates += 1
                continue

        conn.commit()
        conn.close()

        logger.info(
            "Stored publications to database: %d total, %d inserted, %d duplicates",
            len(publications),
            inserted,
            duplicates
        )

        return {
            "success": True,
            "total": len(publications),
            "inserted": inserted,
            "duplicates": duplicates,
            "error": None
        }

    except Exception as e:
        logger.warning("Failed to store publications to database: %s", e)
        return {
            "success": False,
            "total": len(publications),
            "inserted": 0,
            "duplicates": 0,
            "error": str(e)
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
    db_path: str = DEFAULT_DB_PATH
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
            1 if upload_drive else 0
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
                    pub.get("date", "")
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
            pub_runs_inserted
        )

        return {
            "success": True,
            "run_id": run_id,
            "pub_runs_inserted": pub_runs_inserted,
            "error": None
        }

    except Exception as e:
        logger.warning("Failed to store run history: %s", e)
        return {
            "success": False,
            "run_id": run_id,
            "pub_runs_inserted": 0,
            "error": str(e)
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
                "upload_drive": row[10] == 1
            })

        conn.close()
        return results

    except Exception as e:
        logger.warning("Failed to get run history: %s", e)
        return []
