"""Manifest generation and management for run outputs.

Handles creating run manifests and updating latest pointers for daily/weekly runs.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def generate_run_manifest(
    run_id: str,
    run_type: str,
    generated_at: str,
    window_start: str,
    window_end: str,
    total_candidates: int = 0,
    fetched_count: int = 0,
    deduplicated_count: int = 0,
    new_count: int = 0,
    unchanged_count: int = 0,
    output_paths: Optional[Dict[str, str]] = None,
    scoring_info: Optional[Dict] = None,
    active_sources: Optional[List[str]] = None,
    source_stats: Optional[List[dict]] = None,
    config_path: Optional[str] = None,
    config_hash: Optional[str] = None,
    dedupe_stats: Optional[dict] = None,
    drive_output_paths: Optional[Dict[str, str]] = None,
    drive_file_ids: Optional[Dict[str, str]] = None,
) -> dict:
    """Generate a comprehensive run manifest.

    Args:
        run_id: Run identifier (daily-YYYY-MM-DD or weekly-YYYY-WW)
        run_type: Type of run ("daily" or "weekly")
        generated_at: ISO timestamp of generation
        window_start: ISO timestamp of window start
        window_end: ISO timestamp of window end
        total_candidates: Total number of candidate publications
        fetched_count: Number of publications fetched
        deduplicated_count: Number after deduplication
        new_count: Number of new publications
        unchanged_count: Number of unchanged publications
        output_paths: Dictionary of relative local output paths
        scoring_info: Dictionary with relevancy/credibility versions/models
        active_sources: List of active source names
        source_stats: List of source-specific statistics
        config_path: Path to configuration file
        config_hash: SHA256 hash of configuration file
        dedupe_stats: Deduplication statistics
        drive_output_paths: Dictionary of Drive output paths (e.g., "Daily/<run_id>/must_reads.json")
        drive_file_ids: Dictionary of Drive file IDs for direct access

    Returns:
        Manifest dictionary
    """
    manifest = {
        "run_id": run_id,
        "run_type": run_type,
        "generated_at": generated_at,
        "window_start": window_start,
        "window_end": window_end,
        "counts": {
            "total_candidates": total_candidates,
            "fetched": fetched_count,
            "deduplicated": deduplicated_count,
            "new_count": new_count,
            "unchanged_count": unchanged_count,
        },
        "local_output_paths": output_paths or {},
        "output_paths": output_paths or {},  # Keep for backward compatibility
    }

    # Add Drive paths and file IDs if provided
    if drive_output_paths:
        manifest["drive_output_paths"] = drive_output_paths

    if drive_file_ids:
        manifest["drive_file_ids"] = drive_file_ids

    # Optional fields
    if scoring_info:
        manifest["scoring"] = scoring_info

    if active_sources:
        manifest["active_sources"] = active_sources

    if source_stats:
        manifest["source_details"] = source_stats

    if config_path:
        manifest["config"] = {
            "path": config_path,
            "sha256": config_hash or "",
        }

    if dedupe_stats:
        manifest["deduplication"] = {
            "total_fetched_raw": dedupe_stats.get("total_input", 0),
            "deduped_total": dedupe_stats.get("total_output", 0),
            "duplicates_merged": dedupe_stats.get("duplicates_merged", 0),
        }

    return manifest


def save_run_manifest(
    manifest: dict,
    outdir: Path,
) -> Path:
    """Save run manifest to manifests/<run_type>/<run_id>.json.

    Args:
        manifest: Manifest dictionary
        outdir: Base output directory (e.g. "data")

    Returns:
        Path to saved manifest file
    """
    run_id = manifest["run_id"]
    run_type = manifest["run_type"]

    # Create manifests directory structure
    manifests_dir = outdir / "manifests" / run_type
    manifests_dir.mkdir(parents=True, exist_ok=True)

    # Write manifest
    manifest_path = manifests_dir / f"{run_id}.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    logger.info("Saved manifest to %s", manifest_path)
    return manifest_path


def update_latest_pointer(
    manifest: dict,
    outdir: Path,
) -> Path:
    """Update manifests/<run_type>/latest.json to point to this run.

    Args:
        manifest: Manifest dictionary
        outdir: Base output directory (e.g. "data")

    Returns:
        Path to latest pointer file
    """
    run_id = manifest["run_id"]
    run_type = manifest["run_type"]
    run_type_capitalized = run_type.capitalize()  # "daily" -> "Daily", "weekly" -> "Weekly"

    # Create latest pointer with local paths
    latest_pointer = {
        "run_id": run_id,
        "generated_at": manifest["generated_at"],
        "local_output_paths": manifest.get("local_output_paths", {}),
        "output_paths": manifest.get("output_paths", {}),  # Keep for backward compatibility
        "manifest_path": f"manifests/{run_type}/{run_id}.json",
    }

    # Add Drive paths if available
    if "drive_output_paths" in manifest:
        latest_pointer["drive_output_paths"] = manifest["drive_output_paths"]
        latest_pointer["drive_manifest_path"] = f"Manifests/{run_type_capitalized}/{run_id}.json"

    if "drive_file_ids" in manifest:
        latest_pointer["drive_file_ids"] = manifest["drive_file_ids"]

    # Save latest pointer
    manifests_dir = outdir / "manifests" / run_type
    manifests_dir.mkdir(parents=True, exist_ok=True)

    latest_path = manifests_dir / "latest.json"
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(latest_pointer, f, indent=2)

    logger.info("Updated latest pointer: %s", latest_path)
    return latest_path


def get_output_paths(
    run_id: str,
    run_type: str,
    base_dir: str = "data",
) -> Dict[str, str]:
    """Get relative output paths for a run.

    Args:
        run_id: Run identifier
        run_type: Type of run ("daily" or "weekly")
        base_dir: Base directory (default: "data")

    Returns:
        Dictionary of relative paths
    """
    return {
        "must_reads_json": f"{base_dir}/outputs/{run_type}/{run_id}/must_reads.json",
        "report_md": f"{base_dir}/outputs/{run_type}/{run_id}/report.md",
        "new_csv": f"{base_dir}/outputs/{run_type}/{run_id}/new.csv",
        "manifest_json": f"{base_dir}/manifests/{run_type}/{run_id}.json",
        "summaries_json": f"{base_dir}/outputs/{run_type}/{run_id}/summaries.json",
    }


def create_output_directories(
    run_id: str,
    run_type: str,
    outdir: Path,
) -> Path:
    """Create output directory structure for a run.

    Creates: data/outputs/<run_type>/<run_id>/

    Args:
        run_id: Run identifier
        run_type: Type of run ("daily" or "weekly")
        outdir: Base output directory (e.g. Path("data"))

    Returns:
        Path to run output directory
    """
    run_output_dir = outdir / "outputs" / run_type / run_id
    run_output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Created output directory: %s", run_output_dir)
    return run_output_dir
