"""Rerank cache for must-reads LLM scoring.

This module provides cache storage and retrieval for LLM reranking results,
minimizing redundant API calls and improving performance.
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Rerank version constant - increment when prompt/model changes significantly
RERANK_VERSION = "v1"

# Database path
DEFAULT_DB_PATH = "data/db/acitrack.db"


def get_cached_rerank(
    pub_ids: List[str],
    rerank_version: str = RERANK_VERSION,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, dict]:
    """Retrieve cached rerank results for publication IDs.

    Args:
        pub_ids: List of publication IDs to look up
        rerank_version: Version of rerank algorithm (default: RERANK_VERSION)
        db_path: Path to SQLite database

    Returns:
        Dictionary mapping pub_id to cached data:
        {
            "pub_id": {
                "llm_score": float,
                "llm_rank": int,
                "llm_reason": str,
                "llm_why": str,
                "llm_findings": list[str],
                "model": str,
                "created_at": str
            }
        }
    """
    if not pub_ids:
        return {}

    db_file = Path(db_path)
    if not db_file.exists():
        logger.warning("Database not found at %s, no cache available", db_path)
        return {}

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Query cache for these pub_ids
        placeholders = ",".join("?" * len(pub_ids))
        cursor.execute(
            f"""
            SELECT pub_id, model, llm_score, llm_rank, llm_reason, llm_why, llm_findings, created_at
            FROM must_reads_rerank_cache
            WHERE pub_id IN ({placeholders}) AND rerank_version = ?
        """,
            (*pub_ids, rerank_version),
        )

        results = {}
        for row in cursor.fetchall():
            # Parse llm_findings from JSON
            findings = []
            if row["llm_findings"]:
                try:
                    findings = json.loads(row["llm_findings"])
                except json.JSONDecodeError:
                    logger.warning(
                        "Failed to parse llm_findings for pub_id=%s", row["pub_id"]
                    )

            results[row["pub_id"]] = {
                "llm_score": row["llm_score"],
                "llm_rank": row["llm_rank"],
                "llm_reason": row["llm_reason"] or "",
                "llm_why": row["llm_why"] or "",
                "llm_findings": findings,
                "model": row["model"] or "",
                "created_at": row["created_at"] or "",
            }

        conn.close()
        logger.info(
            "Retrieved %d cached rerank results (out of %d requested)",
            len(results),
            len(pub_ids),
        )
        return results

    except Exception as e:
        logger.error("Error retrieving cached rerank results: %s", e)
        return {}


def store_rerank_results(
    results: List[dict],
    model: str,
    rerank_version: str = RERANK_VERSION,
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    """Store rerank results in cache.

    Args:
        results: List of rerank result dicts with keys:
            - pub_id (required)
            - llm_score (required)
            - llm_rank (required)
            - llm_reason (optional)
            - llm_why (optional)
            - llm_findings (optional, list)
        model: Model name used for reranking (e.g., "gpt-4o-mini")
        rerank_version: Version of rerank algorithm (default: RERANK_VERSION)
        db_path: Path to SQLite database

    Returns:
        Dictionary with success status:
        {
            "success": bool,
            "stored_count": int,
            "error": str (if success=False)
        }
    """
    if not results:
        return {"success": True, "stored_count": 0}

    db_file = Path(db_path)
    if not db_file.exists():
        logger.warning("Database not found at %s, cannot store cache", db_path)
        return {
            "success": False,
            "stored_count": 0,
            "error": "Database not found",
        }

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        stored_count = 0
        for result in results:
            pub_id = result.get("pub_id")
            if not pub_id:
                logger.warning("Skipping result without pub_id: %s", result)
                continue

            llm_score = result.get("llm_score", 0.0)
            llm_rank = result.get("llm_rank", 0)
            llm_reason = result.get("llm_reason", "")
            llm_why = result.get("llm_why", "")
            llm_findings = result.get("llm_findings", [])

            # Serialize findings to JSON
            llm_findings_json = json.dumps(llm_findings) if llm_findings else None

            # Insert or replace cache entry
            cursor.execute(
                """
                INSERT OR REPLACE INTO must_reads_rerank_cache
                (pub_id, rerank_version, model, llm_score, llm_rank, llm_reason, llm_why, llm_findings)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    pub_id,
                    rerank_version,
                    model,
                    llm_score,
                    llm_rank,
                    llm_reason,
                    llm_why,
                    llm_findings_json,
                ),
            )
            stored_count += 1

        conn.commit()
        conn.close()

        logger.info("Stored %d rerank results in cache", stored_count)
        return {"success": True, "stored_count": stored_count}

    except Exception as e:
        logger.error("Error storing rerank results: %s", e)
        return {"success": False, "stored_count": 0, "error": str(e)}
