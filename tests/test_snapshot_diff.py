"""Test snapshot diff functionality for change detection."""

import tempfile
from pathlib import Path

from acitrack_types import Publication
from diff.detect_changes import detect_changes


def create_test_publication(id: str, title: str, date: str = "2025-01-01") -> Publication:
    """Helper to create a test publication."""
    return Publication(
        id=id,
        title=title,
        authors=["Test Author"],
        source="Test Source",
        date=date,
        url=f"https://example.com/{id}",
        raw_text="Test abstract",
        summary="Test summary",
        run_id="test_run",
        venue="Test Venue",
    )


def test_first_run_all_new():
    """Test that first run (no snapshot) marks all publications as NEW."""
    with tempfile.TemporaryDirectory() as tmpdir:
        snapshot_dir = str(tmpdir)

        # Create test publications
        pubs = [
            create_test_publication("id1", "Publication 1"),
            create_test_publication("id2", "Publication 2"),
            create_test_publication("id3", "Publication 3"),
        ]

        # First run - no existing snapshot
        result = detect_changes(pubs, snapshot_dir, "run_001")

        # All should be NEW
        assert result["count_new"] == 3
        assert result["count_total"] == 3
        assert len(result["new"]) == 3
        assert len(result["all_with_status"]) == 3

        # Check all have NEW status
        for pub_dict in result["all_with_status"]:
            assert pub_dict["status"] == "NEW"


def test_second_run_mostly_unchanged():
    """Test that second run with same data marks most as UNCHANGED."""
    with tempfile.TemporaryDirectory() as tmpdir:
        snapshot_dir = str(tmpdir)

        # Create test publications for first run
        pubs_run1 = [
            create_test_publication("id1", "Publication 1"),
            create_test_publication("id2", "Publication 2"),
            create_test_publication("id3", "Publication 3"),
        ]

        # First run
        detect_changes(pubs_run1, snapshot_dir, "run_001")

        # Second run with 2 same + 1 new
        pubs_run2 = [
            create_test_publication("id1", "Publication 1"),  # UNCHANGED
            create_test_publication("id2", "Publication 2"),  # UNCHANGED
            create_test_publication("id4", "Publication 4"),  # NEW
        ]

        result = detect_changes(pubs_run2, snapshot_dir, "run_002")

        # Should have 1 NEW, 3 total
        assert result["count_new"] == 1
        assert result["count_total"] == 3
        assert len(result["new"]) == 1
        assert len(result["all_with_status"]) == 3

        # Check NEW publication
        new_pub = result["new"][0]
        assert new_pub.id == "id4"

        # Check statuses
        statuses = {p["id"]: p["status"] for p in result["all_with_status"]}
        assert statuses["id1"] == "UNCHANGED"
        assert statuses["id2"] == "UNCHANGED"
        assert statuses["id4"] == "NEW"


def test_snapshot_persistence():
    """Test that snapshot persists across runs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        snapshot_dir = str(tmpdir)
        snapshot_path = Path(snapshot_dir) / "latest.json"

        # First run
        pubs1 = [create_test_publication("id1", "Pub 1")]
        detect_changes(pubs1, snapshot_dir, "run_001")

        # Snapshot should exist
        assert snapshot_path.exists()

        # Second run
        pubs2 = [
            create_test_publication("id1", "Pub 1"),
            create_test_publication("id2", "Pub 2"),
        ]
        result = detect_changes(pubs2, snapshot_dir, "run_002")

        # Should detect id1 as UNCHANGED
        assert result["count_new"] == 1
        statuses = {p["id"]: p["status"] for p in result["all_with_status"]}
        assert statuses["id1"] == "UNCHANGED"
        assert statuses["id2"] == "NEW"


def test_all_new_after_reset():
    """Test that after snapshot reset, all items are NEW again."""
    with tempfile.TemporaryDirectory() as tmpdir:
        snapshot_dir = str(tmpdir)
        snapshot_path = Path(snapshot_dir) / "latest.json"

        # First run
        pubs = [
            create_test_publication("id1", "Pub 1"),
            create_test_publication("id2", "Pub 2"),
        ]
        detect_changes(pubs, snapshot_dir, "run_001")

        # Delete snapshot (simulate --reset-snapshot)
        snapshot_path.unlink()

        # Second run with same publications
        result = detect_changes(pubs, snapshot_dir, "run_002")

        # All should be NEW again
        assert result["count_new"] == 2
        assert result["count_total"] == 2
        for pub_dict in result["all_with_status"]:
            assert pub_dict["status"] == "NEW"
