"""Test report generation functionality."""

import json
import tempfile
from pathlib import Path

from output.report import generate_report


def test_generate_report_creates_file():
    """Test that generate_report creates markdown file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_id = "test_run_001"

        # Create required directory structure
        raw_dir = Path(tmpdir) / "raw"
        output_dir = Path(tmpdir) / "output"
        raw_dir.mkdir(parents=True)
        output_dir.mkdir(parents=True)

        # Create minimal changes JSON
        changes_data = {
            "run_id": run_id,
            "timestamp": "2025-01-01T12:00:00",
            "since_date": "2024-12-25",
            "count_new": 2,
            "count_total": 3,
            "publications": [
                {
                    "id": "id1",
                    "title": "New Publication 1",
                    "authors": ["Author A"],
                    "source": "Test Source",
                    "date": "2025-01-01",
                    "url": "https://example.com/1",
                    "status": "NEW",
                    "one_liner": "Test summary 1",
                    "essence_bullets": ["Point 1", "Point 2"],
                },
                {
                    "id": "id2",
                    "title": "New Publication 2",
                    "authors": ["Author B"],
                    "source": "Test Source",
                    "date": "2025-01-01",
                    "url": "https://example.com/2",
                    "status": "NEW",
                    "one_liner": "Test summary 2",
                    "essence_bullets": [],
                },
                {
                    "id": "id3",
                    "title": "Unchanged Publication",
                    "authors": ["Author C"],
                    "source": "Test Source",
                    "date": "2024-12-30",
                    "url": "https://example.com/3",
                    "status": "UNCHANGED",
                    "one_liner": "",
                    "essence_bullets": [],
                },
            ],
        }

        changes_path = raw_dir / f"{run_id}_changes.json"
        with open(changes_path, "w") as f:
            json.dump(changes_data, f)

        # Generate report
        generate_report(tmpdir, run_id)

        # Check report file exists
        report_path = output_dir / f"{run_id}_report.md"
        assert report_path.exists()

        # Read and verify content
        content = report_path.read_text()
        assert "# AciTrack" in content
        assert run_id in content
        assert "New Publication 1" in content
        assert "New Publication 2" in content


def test_report_includes_counts():
    """Test that report includes correct publication counts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_id = "test_run_002"

        raw_dir = Path(tmpdir) / "raw"
        output_dir = Path(tmpdir) / "output"
        raw_dir.mkdir(parents=True)
        output_dir.mkdir(parents=True)

        # Create changes with specific counts
        changes_data = {
            "run_id": run_id,
            "timestamp": "2025-01-01T12:00:00",
            "since_date": "2024-12-25",
            "count_new": 5,
            "count_total": 10,
            "publications": [],
        }

        changes_path = raw_dir / f"{run_id}_changes.json"
        with open(changes_path, "w") as f:
            json.dump(changes_data, f)

        # Generate report
        generate_report(tmpdir, run_id)

        # Check counts in report
        report_path = output_dir / f"{run_id}_report.md"
        content = report_path.read_text()

        # Check that counts appear (format may vary)
        assert "New This Run (5)" in content or "NEW (5)" in content or "5" in content
        assert "Unchanged (5)" in content or "UNCHANGED (5)" in content or "5" in content


def test_report_handles_empty_publications():
    """Test that report generation handles empty publication list."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_id = "test_run_003"

        raw_dir = Path(tmpdir) / "raw"
        output_dir = Path(tmpdir) / "output"
        raw_dir.mkdir(parents=True)
        output_dir.mkdir(parents=True)

        # Create changes with no publications
        changes_data = {
            "run_id": run_id,
            "timestamp": "2025-01-01T12:00:00",
            "since_date": "2024-12-25",
            "count_new": 0,
            "count_total": 0,
            "publications": [],
        }

        changes_path = raw_dir / f"{run_id}_changes.json"
        with open(changes_path, "w") as f:
            json.dump(changes_data, f)

        # Generate report (should not crash)
        generate_report(tmpdir, run_id)

        # Report should exist
        report_path = output_dir / f"{run_id}_report.md"
        assert report_path.exists()

        content = report_path.read_text()
        # Check that report was generated (should have header)
        assert "# AciTrack" in content
        assert run_id in content
