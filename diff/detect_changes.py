"""Detect changes between publication snapshots."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from acitrack_types import Publication

logger = logging.getLogger(__name__)


def load_snapshot(snapshot_dir: str) -> Optional[dict]:
    """Load the latest snapshot from disk.

    Args:
        snapshot_dir: Directory containing snapshots

    Returns:
        Snapshot dictionary or None if not found
    """
    snapshot_path = Path(snapshot_dir) / "latest.json"
    if not snapshot_path.exists():
        logger.info("No previous snapshot found at %s", snapshot_path)
        return None

    try:
        with open(snapshot_path, "r") as f:
            snapshot = json.load(f)
        logger.info(
            "Loaded snapshot from %s (run_id: %s, %d publications)",
            snapshot_path,
            snapshot.get("run_id"),
            len(snapshot.get("publication_ids", [])),
        )
        return snapshot
    except Exception as e:
        logger.error("Failed to load snapshot from %s: %s", snapshot_path, e)
        return None


def save_snapshot(
    snapshot_dir: str, run_id: str, publication_ids: list[str]
) -> None:
    """Save current snapshot to disk.

    Args:
        snapshot_dir: Directory to save snapshot
        run_id: Current run ID
        publication_ids: List of publication IDs
    """
    snapshot_path = Path(snapshot_dir) / "latest.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    snapshot = {
        "run_id": run_id,
        "timestamp": datetime.now().isoformat(),
        "publication_ids": publication_ids,
    }

    try:
        with open(snapshot_path, "w") as f:
            json.dump(snapshot, f, indent=2)
        logger.info("Saved snapshot to %s with %d publications", snapshot_path, len(publication_ids))
    except Exception as e:
        logger.error("Failed to save snapshot to %s: %s", snapshot_path, e)


def detect_changes(
    publications: list[Publication], snapshot_dir: str, run_id: str
) -> dict:
    """Detect new and unchanged publications by comparing with previous snapshots.

    Args:
        publications: Current batch of publications
        snapshot_dir: Directory containing previous snapshots
        run_id: Current run ID

    Returns:
        Dictionary with:
        - 'new': List of new publications
        - 'unchanged': List of unchanged publications
        - 'all_with_status': List of dicts with publication data and status field
        - 'count_new': Count of new publications
        - 'count_total': Total count of publications
    """
    logger.info("Detecting changes in %d publications", len(publications))

    # Load previous snapshot
    previous_snapshot = load_snapshot(snapshot_dir)
    previous_ids = set(previous_snapshot.get("publication_ids", [])) if previous_snapshot else set()

    # Get current publication IDs
    current_ids = {pub.id for pub in publications}

    # Determine which are new
    new_ids = current_ids - previous_ids

    # Categorize publications
    new_publications = []
    unchanged_publications = []
    all_with_status = []

    for pub in publications:
        status = "NEW" if pub.id in new_ids else "UNCHANGED"

        # Add to categorized lists
        if status == "NEW":
            new_publications.append(pub)
        else:
            unchanged_publications.append(pub)

        # Create dict with status for output
        pub_dict = {
            "id": pub.id,
            "title": pub.title,
            "authors": pub.authors,
            "source": pub.source,
            "source_names": getattr(pub, "source_names", [pub.source]),
            "date": pub.date,
            "url": pub.url,
            "raw_text": pub.raw_text,
            "summary": pub.summary,
            "run_id": pub.run_id,
            "venue": pub.venue,
            "status": status,
        }
        all_with_status.append(pub_dict)

    # Save new snapshot with current IDs
    save_snapshot(snapshot_dir, run_id, list(current_ids))

    logger.info(
        "Change detection complete - New: %d, Unchanged: %d",
        len(new_publications),
        len(unchanged_publications),
    )

    return {
        "new": new_publications,
        "unchanged": unchanged_publications,
        "all_with_status": all_with_status,
        "count_new": len(new_publications),
        "count_total": len(publications),
    }
