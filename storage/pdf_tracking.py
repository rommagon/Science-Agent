"""Data access helpers for pdf_store and pending_fetch tables.

These tables are created by Alembic migration 004 (Postgres) and SQLite
migration v10. This module hides the SQLite/Postgres dialect differences
behind a small functional API so the Wednesday orchestrator, Thursday
digest, and upload app can share the same access layer.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import date, datetime
from typing import List, Optional

logger = logging.getLogger(__name__)


# --- Connection --------------------------------------------------------

def _is_postgres_url(url: Optional[str]) -> bool:
    return bool(url) and url.startswith("postgresql://")


def get_connection(database_url: Optional[str] = None, sqlite_path: Optional[str] = None):
    """Return a Postgres or SQLite connection depending on configuration.

    Follows the same precedence as digest/data_access.py: explicit
    database_url > DATABASE_URL env var > SQLite fallback.
    """
    url = database_url or os.environ.get("DATABASE_URL")
    if _is_postgres_url(url):
        import psycopg2
        return psycopg2.connect(url)

    path = sqlite_path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "db", "acitrack.db",
    )
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _is_postgres_conn(conn) -> bool:
    # psycopg2 connections have .dsn; sqlite3.Connection does not.
    return hasattr(conn, "dsn")


def _placeholder(conn) -> str:
    return "%s" if _is_postgres_conn(conn) else "?"


# --- pdf_store ---------------------------------------------------------

def upsert_pdf_record(
    conn,
    publication_id: str,
    file_path: str,
    sha256: str,
    license: Optional[str],
    source_api: str,
    bytes_len: int,
) -> None:
    """Insert or replace the PDF record for a publication."""
    ph = _placeholder(conn)
    cursor = conn.cursor()
    if _is_postgres_conn(conn):
        cursor.execute(
            f"""
            INSERT INTO pdf_store
                (publication_id, file_path, sha256, license, source_api,
                 bytes_len, fetched_at)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, CURRENT_TIMESTAMP)
            ON CONFLICT (publication_id) DO UPDATE SET
                file_path = EXCLUDED.file_path,
                sha256 = EXCLUDED.sha256,
                license = EXCLUDED.license,
                source_api = EXCLUDED.source_api,
                bytes_len = EXCLUDED.bytes_len,
                fetched_at = CURRENT_TIMESTAMP
            """,
            (publication_id, file_path, sha256, license, source_api, bytes_len),
        )
    else:
        cursor.execute(
            f"""
            INSERT OR REPLACE INTO pdf_store
                (publication_id, file_path, sha256, license, source_api,
                 bytes_len, fetched_at)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, CURRENT_TIMESTAMP)
            """,
            (publication_id, file_path, sha256, license, source_api, bytes_len),
        )
    conn.commit()


def get_pdf_record(conn, publication_id: str) -> Optional[dict]:
    """Fetch the PDF record for one publication, or None."""
    ph = _placeholder(conn)
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT publication_id, file_path, sha256, license, source_api,
               bytes_len, fetched_at
        FROM pdf_store WHERE publication_id = {ph}
        """,
        (publication_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    # Normalize row→dict for both dialects
    if isinstance(row, sqlite3.Row):
        return dict(row)
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


# --- pending_fetch -----------------------------------------------------

def upsert_pending_fetch(
    conn,
    publication_id: str,
    week_start: date,
    original_url: Optional[str],
    status: str = "pending",
) -> None:
    """Insert (or refresh) a pending_fetch row.

    Sets alerted_at=CURRENT_TIMESTAMP if inserting in 'pending' state.
    On conflict, only status + original_url are refreshed — alerted_at
    is preserved so we can detect already-alerted items.
    """
    ph = _placeholder(conn)
    cursor = conn.cursor()
    if _is_postgres_conn(conn):
        cursor.execute(
            f"""
            INSERT INTO pending_fetch
                (publication_id, week_start, status, original_url, alerted_at)
            VALUES ({ph}, {ph}, {ph}, {ph},
                    CASE WHEN {ph} = 'pending' THEN CURRENT_TIMESTAMP ELSE NULL END)
            ON CONFLICT (publication_id, week_start) DO UPDATE SET
                status = EXCLUDED.status,
                original_url = EXCLUDED.original_url
            """,
            (publication_id, week_start, status, original_url, status),
        )
    else:
        alerted = datetime.utcnow().isoformat() if status == "pending" else None
        cursor.execute(
            f"""
            INSERT INTO pending_fetch
                (publication_id, week_start, status, original_url, alerted_at)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph})
            ON CONFLICT(publication_id, week_start) DO UPDATE SET
                status = excluded.status,
                original_url = excluded.original_url
            """,
            (publication_id, week_start.isoformat(), status, original_url, alerted),
        )
    conn.commit()


def list_pending_fetch(
    conn,
    week_start: Optional[date] = None,
    status: Optional[str] = None,
) -> List[dict]:
    """List pending_fetch rows, optionally filtered by week_start and/or status."""
    ph = _placeholder(conn)
    clauses = []
    params: List = []
    if week_start is not None:
        clauses.append(f"week_start = {ph}")
        params.append(week_start if _is_postgres_conn(conn) else week_start.isoformat())
    if status is not None:
        clauses.append(f"status = {ph}")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT publication_id, week_start, status, original_url,
               alerted_at, uploaded_at, created_at
        FROM pending_fetch {where} ORDER BY created_at
        """,
        params,
    )
    rows = cursor.fetchall()
    if not rows:
        return []
    if isinstance(rows[0], sqlite3.Row):
        return [dict(r) for r in rows]
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, r)) for r in rows]


def mark_uploaded(
    conn,
    publication_id: str,
    week_start: date,
) -> bool:
    """Mark a pending_fetch row as uploaded. Returns True if a row was updated."""
    ph = _placeholder(conn)
    cursor = conn.cursor()
    if _is_postgres_conn(conn):
        cursor.execute(
            f"""
            UPDATE pending_fetch
               SET status = 'uploaded', uploaded_at = CURRENT_TIMESTAMP
             WHERE publication_id = {ph} AND week_start = {ph}
            """,
            (publication_id, week_start),
        )
    else:
        cursor.execute(
            f"""
            UPDATE pending_fetch
               SET status = 'uploaded', uploaded_at = {ph}
             WHERE publication_id = {ph} AND week_start = {ph}
            """,
            (datetime.utcnow().isoformat(), publication_id, week_start.isoformat()),
        )
    conn.commit()
    return cursor.rowcount > 0


def mark_cutoff(
    conn,
    publication_id: str,
    week_start: date,
) -> None:
    """Mark a row as cutoff (Thursday digest sent without this PDF)."""
    ph = _placeholder(conn)
    cursor = conn.cursor()
    if _is_postgres_conn(conn):
        cursor.execute(
            f"""
            UPDATE pending_fetch SET status = 'cutoff'
             WHERE publication_id = {ph} AND week_start = {ph}
               AND status IN ('pending', 'uploaded')
            """,
            (publication_id, week_start),
        )
    else:
        cursor.execute(
            f"""
            UPDATE pending_fetch SET status = 'cutoff'
             WHERE publication_id = {ph} AND week_start = {ph}
               AND status IN ('pending', 'uploaded')
            """,
            (publication_id, week_start.isoformat()),
        )
    conn.commit()


def mark_attached(
    conn,
    publication_id: str,
    week_start: date,
) -> None:
    """Mark a row as attached (picked up by digest; PDF in pdf_store)."""
    ph = _placeholder(conn)
    cursor = conn.cursor()
    if _is_postgres_conn(conn):
        cursor.execute(
            f"""
            UPDATE pending_fetch SET status = 'attached'
             WHERE publication_id = {ph} AND week_start = {ph}
            """,
            (publication_id, week_start),
        )
    else:
        cursor.execute(
            f"""
            UPDATE pending_fetch SET status = 'attached'
             WHERE publication_id = {ph} AND week_start = {ph}
            """,
            (publication_id, week_start.isoformat()),
        )
    conn.commit()
