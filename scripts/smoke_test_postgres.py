#!/usr/bin/env python3
"""Smoke test for PostgreSQL storage.

This script verifies PostgreSQL connectivity and basic operations:
- Connect to database
- Insert a test paper
- Insert a test run
- Store a relevancy event
- Store a tri-model event
- Query back the data
- Clean up test data

Usage:
    export DATABASE_URL="postgresql://acitrack:acitrack@localhost:5432/acitrack"
    python scripts/smoke_test_postgres.py
"""

import logging
import os
import sys
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Run smoke test."""
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        print("\n❌ ERROR: DATABASE_URL environment variable not set")
        print("   Example: export DATABASE_URL='postgresql://acitrack:acitrack@localhost:5432/acitrack'\n")
        sys.exit(1)

    if not database_url.startswith("postgresql://"):
        print("\n❌ ERROR: DATABASE_URL must start with postgresql://\n")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("PostgreSQL Smoke Test")
    print("=" * 70)
    print(f"Database: {database_url}")
    print("=" * 70 + "\n")

    try:
        from storage import pg_store
        from acitrack_types import Publication

        # Test 1: Connection
        logger.info("Test 1: Testing database connection...")
        import psycopg2
        conn = psycopg2.connect(database_url)
        cursor = conn.cursor()
        cursor.execute("SELECT version()")
        version = cursor.fetchone()[0]
        print(f"✅ Connected to PostgreSQL: {version.split(',')[0]}")
        cursor.close()
        conn.close()

        # Test 2: Insert a test paper
        logger.info("Test 2: Inserting test paper...")
        test_pub = Publication(
            id="test_paper_001",
            title="Test Publication for Smoke Test",
            authors=["Test Author"],
            source="test_source",
            date="2026-01-22",
            url="https://example.com/test",
            raw_text="This is a test abstract for smoke testing PostgreSQL storage.",
            summary="",
            run_id="smoke_test_run",
        )

        result = pg_store.store_publications([test_pub], "smoke_test_run", database_url)
        if result["success"]:
            print(f"✅ Inserted test paper: {result['inserted']} inserted, {result['duplicates']} duplicates")
        else:
            print(f"❌ Failed to insert paper: {result['error']}")
            sys.exit(1)

        # Test 3: Insert a test run
        logger.info("Test 3: Inserting test run...")
        result = pg_store.store_run_history(
            run_id="smoke_test_run",
            started_at=datetime.now().isoformat(),
            since_timestamp=datetime.now().isoformat(),
            sources_count=1,
            total_fetched=1,
            total_deduped=1,
            new_count=1,
            unchanged_count=0,
            summarized_count=0,
            max_items_per_source=10,
            upload_drive=False,
            publications_with_status=[
                {
                    "id": "test_paper_001",
                    "status": "NEW",
                    "source": "test_source",
                    "date": "2026-01-22",
                }
            ],
            database_url=database_url,
        )

        if result["success"]:
            print(f"✅ Inserted test run: {result.get('pub_runs_inserted', 0)} run_papers")
        else:
            print(f"❌ Failed to insert run: {result['error']}")
            sys.exit(1)

        # Test 4: Store a relevancy event
        logger.info("Test 4: Storing relevancy event...")
        result = pg_store.store_relevancy_scoring_event(
            run_id="smoke_test_run",
            mode="daily",
            publication_id="test_paper_001",
            source="test_source",
            prompt_version="v1",
            model="gpt-4",
            relevancy_score=85,
            relevancy_reason="Test relevancy scoring",
            confidence="high",
            signals={"test": True},
            input_fingerprint="test_fingerprint",
            raw_response={"test": "response"},
            latency_ms=100,
            cost_usd=0.01,
            database_url=database_url,
        )

        if result["success"]:
            print("✅ Stored relevancy event")
        else:
            print(f"❌ Failed to store relevancy event: {result['error']}")
            sys.exit(1)

        # Test 5: Store a tri-model event
        logger.info("Test 5: Storing tri-model event...")
        result = pg_store.store_tri_model_scoring_event(
            run_id="smoke_test_run",
            mode="tri-model-daily",
            publication_id="test_paper_001",
            title="Test Publication for Smoke Test",
            source="test_source",
            published_date="2026-01-22",
            claude_review={"score": 80},
            gemini_review={"score": 90},
            gpt_eval={"final_score": 85},
            final_relevancy_score=85,
            final_relevancy_reason="Test tri-model scoring",
            final_signals={"test": True},
            final_summary="Test summary",
            agreement_level="high",
            disagreements="None",
            evaluator_rationale="Test rationale",
            confidence="high",
            prompt_versions={"claude": "v1", "gemini": "v1", "gpt": "v1"},
            model_names={"claude": "claude-3", "gemini": "gemini-pro", "gpt": "gpt-4"},
            claude_latency_ms=100,
            gemini_latency_ms=150,
            gpt_latency_ms=200,
            database_url=database_url,
        )

        if result["success"]:
            print("✅ Stored tri-model event")
        else:
            print(f"❌ Failed to store tri-model event: {result['error']}")
            sys.exit(1)

        # Test 6: Query data back
        logger.info("Test 6: Querying data...")
        conn = psycopg2.connect(database_url)
        cursor = conn.cursor()

        # Count papers
        cursor.execute("SELECT COUNT(*) FROM papers WHERE id = 'test_paper_001'")
        paper_count = cursor.fetchone()[0]
        print(f"✅ Found {paper_count} test paper(s)")

        # Count runs
        cursor.execute("SELECT COUNT(*) FROM runs WHERE run_id = 'smoke_test_run'")
        run_count = cursor.fetchone()[0]
        print(f"✅ Found {run_count} test run(s)")

        # Count run_papers
        cursor.execute("SELECT COUNT(*) FROM run_papers WHERE run_id = 'smoke_test_run'")
        run_paper_count = cursor.fetchone()[0]
        print(f"✅ Found {run_paper_count} test run_paper(s)")

        # Count relevancy events
        cursor.execute("SELECT COUNT(*) FROM relevancy_events WHERE run_id = 'smoke_test_run'")
        rel_count = cursor.fetchone()[0]
        print(f"✅ Found {rel_count} relevancy event(s)")

        # Count tri-model events
        cursor.execute("SELECT COUNT(*) FROM tri_model_events WHERE run_id = 'smoke_test_run'")
        tri_count = cursor.fetchone()[0]
        print(f"✅ Found {tri_count} tri-model event(s)")

        # Test 7: Clean up
        logger.info("Test 7: Cleaning up test data...")
        cursor.execute("DELETE FROM tri_model_events WHERE run_id = 'smoke_test_run'")
        cursor.execute("DELETE FROM relevancy_events WHERE run_id = 'smoke_test_run'")
        cursor.execute("DELETE FROM run_papers WHERE run_id = 'smoke_test_run'")
        cursor.execute("DELETE FROM runs WHERE run_id = 'smoke_test_run'")
        cursor.execute("DELETE FROM papers WHERE id = 'test_paper_001'")
        conn.commit()
        print("✅ Cleaned up test data")

        cursor.close()
        conn.close()

        # Final summary
        print("\n" + "=" * 70)
        print("Smoke Test Summary")
        print("=" * 70)
        print("✅ All tests passed!")
        print("=" * 70 + "\n")

    except ImportError as e:
        print(f"\n❌ ERROR: Failed to import required modules: {e}")
        print("   Make sure psycopg2-binary is installed: pip install psycopg2-binary\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: Smoke test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
