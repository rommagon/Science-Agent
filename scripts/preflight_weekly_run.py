#!/usr/bin/env python3
"""Preflight checks for weekly tri-model readiness (read-only)."""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List

import psycopg2
from psycopg2.extras import RealDictCursor


def _pick_first(columns: List[str], candidates: List[str]) -> str:
    for c in candidates:
        if c in columns:
            return c
    return ""


def main() -> int:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url.startswith("postgresql://"):
        print("FAIL: DATABASE_URL must be set to a postgresql:// URL")
        return 1

    conn = psycopg2.connect(database_url)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Publications schema check
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'publications'
        ORDER BY ordinal_position
        """
    )
    pub_cols = [r["column_name"] for r in cur.fetchall()]

    pub_id_col = _pick_first(pub_cols, ["publication_id", "id"])
    title_col = _pick_first(pub_cols, ["title"])
    doi_col = _pick_first(pub_cols, ["doi"])
    pmid_col = _pick_first(pub_cols, ["pmid"])
    canonical_col = _pick_first(pub_cols, ["canonical_url"])
    source_col = _pick_first(pub_cols, ["source"])
    venue_col = _pick_first(pub_cols, ["venue", "venue_name"])
    date_col = _pick_first(pub_cols, ["published_date", "published_at"])
    text_cols = [c for c in ["raw_text", "abstract", "summary"] if c in pub_cols]

    required_pub_ok = bool(pub_id_col and title_col and (doi_col or pmid_col or canonical_col) and text_cols)

    cur.execute(
        """
        SELECT is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name='publications' AND column_name='created_at'
        """
    )
    created_at_meta = cur.fetchone()
    created_at_requires_app_value = False
    if created_at_meta:
        created_at_requires_app_value = (
            created_at_meta["is_nullable"] == "NO" and created_at_meta["column_default"] is None
        )

    select_cols = [pub_id_col, title_col, source_col, venue_col, date_col, doi_col, pmid_col, canonical_col] + text_cols
    select_cols = [c for c in select_cols if c]
    cur.execute(
        f"SELECT {', '.join(select_cols)} FROM publications ORDER BY {date_col or pub_id_col} DESC LIMIT 50"
    )
    sample_rows = cur.fetchall()

    missing_id = 0
    missing_title = 0
    missing_identity = 0
    missing_text = 0

    for row in sample_rows:
        if not row.get(pub_id_col):
            missing_id += 1
        if not row.get(title_col):
            missing_title += 1
        if not (row.get(canonical_col) if canonical_col else None) and not (row.get(doi_col) if doi_col else None) and not (row.get(pmid_col) if pmid_col else None):
            missing_identity += 1
        has_text = any(bool(row.get(tc)) for tc in text_cols)
        if not has_text:
            missing_text += 1

    # Tri-model events table + schema check
    cur.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema='public' AND table_name IN ('tri_model_events', 'tri_model_scoring_events')
        """
    )
    table_rows = cur.fetchall()
    tri_table = table_rows[0]["table_name"] if table_rows else ""

    tri_ok = False
    has_rating_col = False
    has_raw_payloads = False
    if tri_table:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s
            """,
            (tri_table,),
        )
        tri_cols = [r["column_name"] for r in cur.fetchall()]

        required_tri = ["publication_id", "run_id", "title", "final_relevancy_score"]
        tri_ok = all(c in tri_cols for c in required_tri)
        has_rating_col = "final_relevancy_rating_0_3" in tri_cols
        has_raw_payloads = all(c in tri_cols for c in ["claude_review_json", "gemini_review_json", "gpt_eval_json"])

    coverage_ok = (missing_id == 0 and missing_title == 0 and missing_identity == 0)
    pass_ok = required_pub_ok and tri_ok

    print("Preflight Weekly Run")
    print(f"Database: PostgreSQL (DATABASE_URL configured)")
    print(f"publications schema present: {required_pub_ok}")
    print(f"publications coverage ok (sample): {coverage_ok}")
    print(f"sample_size: {len(sample_rows)}")
    print(f"publications.created_at requires app-side value: {created_at_requires_app_value}")
    print(f"missing publication_id: {missing_id}")
    print(f"missing title: {missing_title}")
    print(f"missing canonical_url/doi/pmid: {missing_identity}")
    print(f"missing text(raw_text/abstract/summary): {missing_text}")
    print(f"tri_model table: {tri_table or 'NOT FOUND'}")
    print(f"tri_model schema present: {tri_ok}")
    print(f"tri_model has relevancy_rating_0_3 column: {has_rating_col}")
    print(f"tri_model has raw reviewer/eval JSON payload columns: {has_raw_payloads}")
    print(f"RESULT: {'PASS' if pass_ok else 'FAIL'}")

    conn.close()
    return 0 if pass_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
