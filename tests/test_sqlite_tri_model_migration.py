"""Tests for tri-model SQLite schema migration.

This test verifies that the credibility column migration is idempotent
and works correctly with older database schemas.
"""

import sqlite3
import sys
from pathlib import Path
import tempfile
import os

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_tri_model_migration_from_old_schema():
    """Test that credibility columns are added to existing tri_model_scoring_events table."""
    from storage.sqlite_store import _init_schema, store_tri_model_scoring_event

    # Create temporary database
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = tmp.name

    try:
        # Step 1: Create old schema WITHOUT credibility columns
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Create schema version table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("INSERT INTO schema_version (version) VALUES (6)")

        # Create old tri_model_scoring_events table WITHOUT credibility columns
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(run_id, publication_id)
            )
        """)
        conn.commit()

        # Verify old schema doesn't have credibility columns
        cursor.execute("PRAGMA table_info(tri_model_scoring_events)")
        old_columns = [row[1] for row in cursor.fetchall()]
        assert "credibility_score" not in old_columns, "Old schema should not have credibility_score"
        assert "credibility_reason" not in old_columns, "Old schema should not have credibility_reason"

        print("✓ Created old schema without credibility columns")
        conn.close()

        # Step 2: Initialize schema (should add credibility columns)
        conn = sqlite3.connect(db_path)
        _init_schema(conn)
        conn.close()

        print("✓ Ran schema initialization/migration")

        # Step 3: Verify credibility columns were added
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(tri_model_scoring_events)")
        new_columns = [row[1] for row in cursor.fetchall()]

        assert "credibility_score" in new_columns, "Migration should add credibility_score"
        assert "credibility_reason" in new_columns, "Migration should add credibility_reason"
        assert "credibility_confidence" in new_columns, "Migration should add credibility_confidence"
        assert "credibility_signals_json" in new_columns, "Migration should add credibility_signals_json"

        print("✓ Verified credibility columns were added")
        conn.close()

        # Step 4: Test that inserts work with credibility data
        result = store_tri_model_scoring_event(
            run_id="test-run",
            mode="tri-model-daily",
            publication_id="test-pub-123",
            title="Test Paper",
            source="PubMed",
            published_date="2026-01-24",
            claude_review={"success": True, "review": {}},
            gemini_review={"success": True, "review": {}},
            gpt_eval={"success": True, "evaluation": {}},
            final_relevancy_score=85,
            final_relevancy_reason="Relevant to early detection",
            final_signals={},
            final_summary="Test summary",
            agreement_level="high",
            disagreements="None",
            evaluator_rationale="Test rationale",
            confidence="high",
            prompt_versions={"claude": "v1", "gemini": "v1", "gpt": "v1"},
            model_names={"claude": "haiku", "gemini": "flash", "gpt": "gpt-4o-mini"},
            claude_latency_ms=100,
            gemini_latency_ms=150,
            gpt_latency_ms=200,
            credibility_score=72,
            credibility_reason="Peer-reviewed prospective study",
            credibility_confidence="high",
            credibility_signals={"peer_reviewed": True, "preprint": False},
            db_path=db_path,
        )

        assert result["success"], f"Insert should succeed: {result.get('error')}"
        print("✓ Successfully inserted event with credibility data")

        # Step 5: Verify data was stored correctly
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT credibility_score, credibility_reason, credibility_confidence, credibility_signals_json
            FROM tri_model_scoring_events
            WHERE publication_id = ?
        """, ("test-pub-123",))
        row = cursor.fetchone()

        assert row is not None, "Event should be stored"
        assert row[0] == 72, f"credibility_score should be 72, got {row[0]}"
        assert row[1] == "Peer-reviewed prospective study", f"credibility_reason should match"
        assert row[2] == "high", f"credibility_confidence should be high"
        assert '"peer_reviewed": true' in row[3].lower(), "credibility_signals should contain peer_reviewed"

        print("✓ Verified stored data is correct")
        conn.close()

        print("\n✅ All migration tests passed!")

    finally:
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_idempotent_migration():
    """Test that running migration multiple times is safe."""
    from storage.sqlite_store import _init_schema

    # Create temporary database
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = tmp.name

    try:
        # Initialize schema 3 times - should be idempotent
        for i in range(3):
            conn = sqlite3.connect(db_path)
            _init_schema(conn)
            conn.close()
            print(f"✓ Schema initialization {i+1}/3 completed")

        # Verify final state
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(tri_model_scoring_events)")
        columns = [row[1] for row in cursor.fetchall()]

        # Count credibility columns (should only appear once each)
        cred_score_count = columns.count("credibility_score")
        assert cred_score_count == 1, f"credibility_score should appear exactly once, found {cred_score_count}"

        print("✓ Verified columns only added once (idempotent)")
        conn.close()

        print("\n✅ Idempotency test passed!")

    finally:
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)


if __name__ == "__main__":
    print("Running tri-model migration tests...\n")
    test_tri_model_migration_from_old_schema()
    print()
    test_idempotent_migration()
