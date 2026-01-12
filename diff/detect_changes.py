"""Detect changes between publication snapshots.

SNAPSHOT MODE (controlled by ACITRACK_SNAPSHOT_MODE env var):
- "merge" (default): Global cumulative snapshot - current run IDs are MERGED with previous snapshot,
  so smaller runs do NOT shrink the snapshot. This is the safe production mode.
- "replace": Legacy mode - snapshot becomes exactly the current run IDs (old behavior).
  Use this for testing or when you explicitly want to reset the corpus.

Example scenario with merge mode:
- Run A: 100 papers → snapshot contains 100 IDs
- Run B: 10 papers (subset) → snapshot still contains 100 IDs (prev 90 kept, 10 updated)
- Run C: 15 papers (5 new, 10 overlap) → snapshot contains 105 IDs (prev 90 kept, 10 updated, 5 new)
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from acitrack_types import Publication

logger = logging.getLogger(__name__)

# Snapshot mode: "merge" (default, safe) or "replace" (legacy, test-only)
SNAPSHOT_MODE = os.getenv("ACITRACK_SNAPSHOT_MODE", "merge").lower()


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
    snapshot_dir: str,
    run_id: str,
    publication_ids: list[str],
    previous_ids: Optional[set[str]] = None,
) -> None:
    """Save current snapshot to disk.

    In merge mode (default), the snapshot is the UNION of previous IDs and current IDs.
    In replace mode, the snapshot is exactly the current IDs (legacy behavior).

    Args:
        snapshot_dir: Directory to save snapshot
        run_id: Current run ID
        publication_ids: List of publication IDs from current run
        previous_ids: Optional set of IDs from previous snapshot (for merge mode)
    """
    snapshot_path = Path(snapshot_dir) / "latest.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    # Determine final ID list based on snapshot mode
    if SNAPSHOT_MODE == "merge" and previous_ids is not None:
        # Merge mode: union of previous and current
        current_id_set = set(publication_ids)
        merged_ids = previous_ids | current_id_set

        # Calculate merge stats
        ids_kept = len(previous_ids - current_id_set)  # Previous IDs not in current run
        ids_updated = len(previous_ids & current_id_set)  # IDs in both (updated)
        ids_added = len(current_id_set - previous_ids)  # New IDs

        final_ids = sorted(merged_ids)  # Sort for consistency

        logger.info(
            "Snapshot merge: prev=%d current=%d → merged=%d (kept=%d updated=%d added=%d)",
            len(previous_ids),
            len(publication_ids),
            len(merged_ids),
            ids_kept,
            ids_updated,
            ids_added,
        )
    else:
        # Replace mode or no previous snapshot
        final_ids = publication_ids
        if SNAPSHOT_MODE == "replace":
            logger.info("Snapshot replace mode: saving %d publication IDs from current run", len(final_ids))

    snapshot = {
        "run_id": run_id,
        "timestamp": datetime.now().isoformat(),
        "publication_ids": final_ids,
        "snapshot_mode": SNAPSHOT_MODE,
    }

    try:
        with open(snapshot_path, "w") as f:
            json.dump(snapshot, f, indent=2)
        logger.info("Saved snapshot to %s with %d publications", snapshot_path, len(final_ids))
    except Exception as e:
        logger.error("Failed to save snapshot to %s: %s", snapshot_path, e)


def detect_changes(
    publications: list[Publication], snapshot_dir: str, run_id: str
) -> dict:
    """Detect new and unchanged publications by comparing with previous snapshots.

    In merge mode (default), the snapshot is cumulative - smaller runs won't shrink it.
    In replace mode, the snapshot becomes exactly the current run (legacy behavior).

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
    logger.info("Detecting changes in %d publications (mode=%s)", len(publications), SNAPSHOT_MODE)

    # Load previous snapshot
    previous_snapshot = load_snapshot(snapshot_dir)
    previous_ids = set(previous_snapshot.get("publication_ids", [])) if previous_snapshot else set()

    if previous_snapshot:
        logger.info("Previous snapshot: %d publications", len(previous_ids))
    else:
        logger.info("No previous snapshot - all publications will be marked as NEW")

    # Get current publication IDs
    current_ids = {pub.id for pub in publications}
    logger.info("Current run: %d publications", len(current_ids))

    # Determine which are new (not in previous snapshot)
    new_ids = current_ids - previous_ids

    logger.info("New in this run: %d publications", len(new_ids))

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

    # Save snapshot (merge mode will keep previous IDs, replace mode will overwrite)
    save_snapshot(snapshot_dir, run_id, list(current_ids), previous_ids if previous_ids else None)

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
