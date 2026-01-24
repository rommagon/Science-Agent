#!/usr/bin/env python3
"""Migrate data from SQLite to PostgreSQL.

This script reads data from the SQLite database (data/db/acitrack.db) and
writes it to the PostgreSQL database specified by DATABASE_URL.

Usage:
    export DATABASE_URL="postgresql://acitrack:acitrack@localhost/acitrack"
    python scripts/migrate_sqlite_to_postgres.py

Tables migrated:
- publications -> papers (with run_papers associations)
- runs -> runs
- relevancy_scoring_events -> relevancy_events
- tri_model_scoring_events -> tri_model_events
"""

import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_sqlite_connection(db_path: str):
    """Get SQLite connection."""
    if not Path(db_path).exists():
        logger.error("SQLite database not found: %s", db_path)
        sys.exit(1)

    return sqlite3.connect(db_path)


def get_postgres_connection(database_url: str):
    """Get PostgreSQL connection."""
    try:
        return psycopg2.connect(database_url)
    except Exception as e:
        logger.error("Failed to connect to PostgreSQL: %s", e)
        sys.exit(1)


def migrate_publications_to_papers(sqlite_conn, pg_conn):
    """Migrate publications table to papers table.

    Args:
        sqlite_conn: SQLite connection
        pg_conn: PostgreSQL connection

    Returns:
        Number of rows migrated
    """
    logger.info("Migrating publications -> papers...")

    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()

    # Fetch all publications from SQLite
    sqlite_cursor.execute("""
        SELECT
            id, title, authors, source, venue, published_date,
            url, raw_text, summary, run_id, source_names,
            doi, citation_count, citations_per_year, venue_name, pub_type,
            relevance_score, credibility_score, main_interesting_fact,
            relevance_to_spotitearly, modality_tags, sample_size,
            study_type, key_metrics, sponsor_flag
        FROM publications
        ORDER BY created_at
    """)

    rows = sqlite_cursor.fetchall()
    logger.info("Found %d publications in SQLite", len(rows))

    if not rows:
        logger.info("No publications to migrate")
        return 0

    # Insert into PostgreSQL papers table
    insert_data = []
    for row in rows:
        insert_data.append(row)

    execute_values(pg_cursor, """
        INSERT INTO papers (
            id, title, authors, source, venue, published_at,
            url, raw_text, summary, run_id, source_names,
            doi, citation_count, citations_per_year, venue_name, pub_type,
            relevance_score, credibility_score, main_interesting_fact,
            relevance_to_spotitearly, modality_tags, sample_size,
            study_type, key_metrics, sponsor_flag
        ) VALUES %s
        ON CONFLICT (id) DO NOTHING
    """, insert_data)

    pg_conn.commit()
    logger.info("Migrated %d publications to papers table", len(rows))

    return len(rows)


def migrate_pub_runs_to_run_papers(sqlite_conn, pg_conn):
    """Migrate pub_runs table to run_papers table.

    Args:
        sqlite_conn: SQLite connection
        pg_conn: PostgreSQL connection

    Returns:
        Number of rows migrated
    """
    logger.info("Migrating pub_runs -> run_papers...")

    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()

    # Check if pub_runs table exists
    sqlite_cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='pub_runs'
    """)

    if not sqlite_cursor.fetchone():
        logger.info("pub_runs table not found in SQLite, skipping")
        return 0

    # Fetch all pub_runs from SQLite
    sqlite_cursor.execute("""
        SELECT run_id, pub_id, status, source, published_date
        FROM pub_runs
    """)

    rows = sqlite_cursor.fetchall()
    logger.info("Found %d pub_runs in SQLite", len(rows))

    if not rows:
        logger.info("No pub_runs to migrate")
        return 0

    # Insert into PostgreSQL run_papers table
    execute_values(pg_cursor, """
        INSERT INTO run_papers (run_id, pub_id, status, source, published_at)
        VALUES %s
        ON CONFLICT (run_id, pub_id) DO NOTHING
    """, rows)

    pg_conn.commit()
    logger.info("Migrated %d pub_runs to run_papers table", len(rows))

    return len(rows)


def migrate_runs(sqlite_conn, pg_conn):
    """Migrate runs table.

    Args:
        sqlite_conn: SQLite connection
        pg_conn: PostgreSQL connection

    Returns:
        Number of rows migrated
    """
    logger.info("Migrating runs table...")

    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()

    # Fetch all runs from SQLite
    sqlite_cursor.execute("""
        SELECT
            run_id, started_at, since_timestamp, max_items_per_source,
            sources_count, total_fetched, total_deduped, new_count,
            unchanged_count, summarized_count, upload_drive
        FROM runs
        ORDER BY started_at
    """)

    rows = sqlite_cursor.fetchall()
    logger.info("Found %d runs in SQLite", len(rows))

    if not rows:
        logger.info("No runs to migrate")
        return 0

    # Insert into PostgreSQL runs table
    execute_values(pg_cursor, """
        INSERT INTO runs (
            run_id, started_at, since_timestamp, max_items_per_source,
            sources_count, total_fetched, total_deduped, new_count,
            unchanged_count, summarized_count, upload_drive
        ) VALUES %s
        ON CONFLICT (run_id) DO NOTHING
    """, rows)

    pg_conn.commit()
    logger.info("Migrated %d runs", len(rows))

    return len(rows)


def migrate_relevancy_events(sqlite_conn, pg_conn):
    """Migrate relevancy_scoring_events to relevancy_events.

    Args:
        sqlite_conn: SQLite connection
        pg_conn: PostgreSQL connection

    Returns:
        Number of rows migrated
    """
    logger.info("Migrating relevancy_scoring_events -> relevancy_events...")

    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()

    # Check if table exists
    sqlite_cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='relevancy_scoring_events'
    """)

    if not sqlite_cursor.fetchone():
        logger.info("relevancy_scoring_events table not found in SQLite, skipping")
        return 0

    # Fetch all events from SQLite
    sqlite_cursor.execute("""
        SELECT
            run_id, mode, publication_id, source, prompt_version, model,
            relevancy_score, relevancy_reason, confidence, signals_json,
            input_fingerprint, raw_response_json, latency_ms, cost_usd
        FROM relevancy_scoring_events
        ORDER BY created_at
    """)

    rows = sqlite_cursor.fetchall()
    logger.info("Found %d relevancy events in SQLite", len(rows))

    if not rows:
        logger.info("No relevancy events to migrate")
        return 0

    # Insert into PostgreSQL relevancy_events table
    execute_values(pg_cursor, """
        INSERT INTO relevancy_events (
            run_id, mode, publication_id, source, prompt_version, model,
            relevancy_score, relevancy_reason, confidence, signals_json,
            input_fingerprint, raw_response_json, latency_ms, cost_usd
        ) VALUES %s
        ON CONFLICT (run_id, publication_id, prompt_version) DO NOTHING
    """, rows)

    pg_conn.commit()
    logger.info("Migrated %d relevancy events", len(rows))

    return len(rows)


def migrate_tri_model_events(sqlite_conn, pg_conn):
    """Migrate tri_model_scoring_events to tri_model_events.

    Args:
        sqlite_conn: SQLite connection
        pg_conn: PostgreSQL connection

    Returns:
        Number of rows migrated
    """
    logger.info("Migrating tri_model_scoring_events -> tri_model_events...")

    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()

    # Check if table exists
    sqlite_cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='tri_model_scoring_events'
    """)

    if not sqlite_cursor.fetchone():
        logger.info("tri_model_scoring_events table not found in SQLite, skipping")
        return 0

    # Fetch all events from SQLite
    sqlite_cursor.execute("""
        SELECT
            run_id, mode, publication_id, title, source, published_date,
            claude_review_json, gemini_review_json, gpt_eval_json,
            final_relevancy_score, final_relevancy_reason, final_signals_json,
            final_summary, agreement_level, disagreements, evaluator_rationale,
            confidence, prompt_versions_json, model_names_json,
            claude_latency_ms, gemini_latency_ms, gpt_latency_ms
        FROM tri_model_scoring_events
        ORDER BY created_at
    """)

    rows = sqlite_cursor.fetchall()
    logger.info("Found %d tri-model events in SQLite", len(rows))

    if not rows:
        logger.info("No tri-model events to migrate")
        return 0

    # Insert into PostgreSQL tri_model_events table
    execute_values(pg_cursor, """
        INSERT INTO tri_model_events (
            run_id, mode, publication_id, title, source, published_date,
            claude_review_json, gemini_review_json, gpt_eval_json,
            final_relevancy_score, final_relevancy_reason, final_signals_json,
            final_summary, agreement_level, disagreements, evaluator_rationale,
            confidence, prompt_versions_json, model_names_json,
            claude_latency_ms, gemini_latency_ms, gpt_latency_ms
        ) VALUES %s
        ON CONFLICT (run_id, publication_id) DO NOTHING
    """, rows)

    pg_conn.commit()
    logger.info("Migrated %d tri-model events", len(rows))

    return len(rows)


def main():
    """Main migration entrypoint."""
    # Get database URLs
    sqlite_db_path = os.getenv("SQLITE_DB_PATH", "data/db/acitrack.db")
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        logger.error("DATABASE_URL environment variable not set")
        print("\n❌ ERROR: DATABASE_URL environment variable not set")
        print("   Example: export DATABASE_URL='postgresql://acitrack:acitrack@localhost/acitrack'\n")
        sys.exit(1)

    if not database_url.startswith("postgresql://"):
        logger.error("DATABASE_URL must start with postgresql://")
        print("\n❌ ERROR: DATABASE_URL must start with postgresql://\n")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("SQLite to PostgreSQL Migration")
    print("=" * 70)
    print(f"SQLite DB:   {sqlite_db_path}")
    print(f"Postgres DB: {database_url}")
    print("=" * 70 + "\n")

    # Connect to databases
    sqlite_conn = get_sqlite_connection(sqlite_db_path)
    pg_conn = get_postgres_connection(database_url)

    try:
        # Run migrations
        papers_count = migrate_publications_to_papers(sqlite_conn, pg_conn)
        run_papers_count = migrate_pub_runs_to_run_papers(sqlite_conn, pg_conn)
        runs_count = migrate_runs(sqlite_conn, pg_conn)
        relevancy_count = migrate_relevancy_events(sqlite_conn, pg_conn)
        tri_model_count = migrate_tri_model_events(sqlite_conn, pg_conn)

        # Print summary
        print("\n" + "=" * 70)
        print("Migration Summary")
        print("=" * 70)
        print(f"Papers:           {papers_count} rows")
        print(f"Run Papers:       {run_papers_count} rows")
        print(f"Runs:             {runs_count} rows")
        print(f"Relevancy Events: {relevancy_count} rows")
        print(f"Tri-Model Events: {tri_model_count} rows")
        print("=" * 70 + "\n")

        print("✅ Migration completed successfully\n")

    except Exception as e:
        logger.error("Migration failed: %s", e)
        print(f"\n❌ ERROR: Migration failed: {e}\n")
        sys.exit(1)
    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    main()
