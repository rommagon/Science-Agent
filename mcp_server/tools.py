"""Pure tool implementations for the Science Agent MCP server.

These functions deliberately have no `mcp.*` imports — they're plain Python so
they can be unit-tested in isolation and reused across the stdio transport
(`mcp_server.server`) and the HTTP transport (`mcp_server.http_app`).

Two tools live here:

- ``search_publications_tool`` — semantic search wrapping
  ``acitrack.semantic_search.search_publications``, hydrating each hit with
  full metadata (summary, scores, best link).
- ``get_publication_tool`` — fetch a single publication record by id.

Both return plain dicts that callers can ``json.dumps`` for the wire.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_HYDRATE_COLUMNS = (
    "title",
    "authors",
    "source",
    "venue",
    "published_date",
    "url",
    "canonical_url",
    "doi",
    "pmid",
    "raw_text",
    "summary",
    "final_summary",
    "final_relevancy_score",
    "final_relevancy_reason",
    "claude_score",
    "gemini_score",
    "credibility_score",
    "credibility_reason",
    "agreement_level",
    "confidence",
)


def _hydrate_publications(pub_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Return ``{publication_id: row_dict}`` for the given ids.

    Schema-tolerant: only selects columns that actually exist in the
    ``publications`` table (PK column name varies — see CLAUDE.md memory).
    """
    if not pub_ids:
        return {}

    from storage.store import is_postgres

    if is_postgres():
        return _hydrate_publications_pg(pub_ids)
    return _hydrate_publications_sqlite(pub_ids)


def _hydrate_publications_pg(pub_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    from storage.pg_store import (
        _get_connection,
        _get_publications_table_metadata,
        _put_connection,
    )
    from storage.store import get_database_url

    database_url = get_database_url()
    conn = _get_connection(database_url)
    try:
        columns, pk_column, _, _ = _get_publications_table_metadata(conn, database_url)
        if not pk_column:
            logger.warning("publications table missing recognizable PK column")
            return {}

        select_cols = [pk_column] + [c for c in _HYDRATE_COLUMNS if c in columns]
        cursor = conn.cursor()
        placeholders = ",".join(["%s"] * len(pub_ids))
        cursor.execute(
            f"SELECT {', '.join(select_cols)} FROM publications "
            f"WHERE {pk_column} IN ({placeholders})",
            pub_ids,
        )
        rows = cursor.fetchall()
        cursor.close()
    finally:
        _put_connection(conn)

    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        d = dict(zip(select_cols, row))
        pub_id = d.get(pk_column)
        if pub_id is None:
            continue
        d["publication_id"] = pub_id
        out[str(pub_id)] = d
    return out


def _hydrate_publications_sqlite(pub_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    db_path = os.environ.get("ACITRACK_DB_PATH", "data/db/acitrack.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(publications)")
        existing = {r[1] for r in cur.fetchall()}
        select_cols = ["id"] + [c for c in _HYDRATE_COLUMNS if c in existing]
        placeholders = ",".join(["?"] * len(pub_ids))
        cur.execute(
            f"SELECT {', '.join(select_cols)} FROM publications "
            f"WHERE id IN ({placeholders})",
            pub_ids,
        )
        out: Dict[str, Dict[str, Any]] = {}
        for row in cur.fetchall():
            d = dict(row)
            pub_id = d.get("id")
            if pub_id is None:
                continue
            d["publication_id"] = pub_id
            out[str(pub_id)] = d
        return out
    finally:
        conn.close()


def _format_pub(pub: Dict[str, Any], include_full: bool = False) -> Dict[str, Any]:
    """Build the canonical wire shape for a publication."""
    from digest.data_access import _build_link

    out: Dict[str, Any] = {
        "publication_id": pub.get("publication_id") or pub.get("id"),
        "title": pub.get("title"),
        "authors": pub.get("authors"),
        "venue": pub.get("venue") or pub.get("source"),
        "source": pub.get("source"),
        "published_date": pub.get("published_date"),
        "summary": pub.get("final_summary") or pub.get("summary"),
        "link": _build_link(pub),
        "relevancy_score": pub.get("final_relevancy_score"),
        "credibility_score": pub.get("credibility_score"),
    }
    if include_full:
        out.update(
            {
                "raw_text": pub.get("raw_text"),
                "claude_score": pub.get("claude_score"),
                "gemini_score": pub.get("gemini_score"),
                "agreement_level": pub.get("agreement_level"),
                "confidence": pub.get("confidence"),
                "relevancy_reason": pub.get("final_relevancy_reason"),
                "credibility_reason": pub.get("credibility_reason"),
                "doi": pub.get("doi"),
                "pmid": pub.get("pmid"),
            }
        )
    return out


def search_publications_tool(
    query: str,
    top_k: int = 10,
    since_days: Optional[int] = 365,
    min_relevancy_score: Optional[float] = None,
) -> Dict[str, Any]:
    """Semantic search over the corpus.

    Args:
        query: Topic, question, or draft excerpt to search for.
        top_k: Max results to return (after relevancy filter).
        since_days: Restrict to publications from the last N days. ``None`` or
            ``0`` disables the date filter.
        min_relevancy_score: If set, drop hits whose stored
            ``final_relevancy_score`` is below this threshold.

    Returns:
        ``{"query": ..., "results": [...], "since_days": ...}`` — results are
        ranked by semantic similarity (descending).
    """
    from acitrack.semantic_search import search_publications as _semantic_search

    if not query or not query.strip():
        return {"query": query, "results": [], "error": "empty query"}

    top_k = max(1, min(int(top_k), 25))
    since = int(since_days) if since_days else None
    if since == 0:
        since = None

    # Cast a wider net when post-filtering by relevancy so the final list has
    # a fighting chance of reaching top_k.
    raw_top_k = top_k * 4 if min_relevancy_score is not None else top_k

    hits = _semantic_search(query=query, top_k=raw_top_k, since_days=since)
    if not hits:
        return {
            "query": query,
            "since_days": since,
            "min_relevancy_score": min_relevancy_score,
            "results": [],
        }

    pub_ids = [str(h["publication_id"]) for h in hits]
    hydrated = _hydrate_publications(pub_ids)
    similarities = {str(h["publication_id"]): h["similarity"] for h in hits}

    results: List[Dict[str, Any]] = []
    for pid in pub_ids:
        pub = hydrated.get(pid)
        if not pub:
            continue
        rel = pub.get("final_relevancy_score")
        if min_relevancy_score is not None:
            if rel is None or rel < min_relevancy_score:
                continue
        formatted = _format_pub(pub)
        formatted["similarity"] = round(float(similarities.get(pid, 0.0)), 4)
        results.append(formatted)
        if len(results) >= top_k:
            break

    return {
        "query": query,
        "since_days": since,
        "min_relevancy_score": min_relevancy_score,
        "results": results,
    }


def get_publication_tool(publication_id: str) -> Dict[str, Any]:
    """Fetch one publication by id (full record, including raw_text)."""
    if not publication_id:
        return {"error": "publication_id is required"}

    hydrated = _hydrate_publications([str(publication_id)])
    pub = hydrated.get(str(publication_id))
    if not pub:
        return {"error": "not found", "publication_id": publication_id}

    return _format_pub(pub, include_full=True)
