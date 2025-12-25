#!/usr/bin/env python3
"""Main CLI entrypoint for acitrack."""

import argparse
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import yaml

from diff.detect_changes import detect_changes
from ingest.fetch import fetch_publications
from output.report import generate_report
from summarize.summarize import summarize_publications

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def load_sources(config_path: str) -> list[dict]:
    """Load source configurations from YAML file.

    Args:
        config_path: Path to sources.yaml configuration file

    Returns:
        List of source configuration dictionaries
    """
    config_file = Path(config_path)
    if not config_file.exists():
        logger.error("Configuration file not found: %s", config_path)
        sys.exit(1)

    with open(config_file, "r") as f:
        config = yaml.safe_load(f)

    sources = config.get("sources", [])
    if not sources:
        logger.warning("No sources configured in %s", config_path)

    return sources


def main() -> None:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="acitrack - Track and summarize cancer research publications"
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=7,
        help="Fetch publications from the last N days (default: 7)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/sources.yaml",
        help="Path to sources configuration file (default: config/sources.yaml)",
    )
    parser.add_argument(
        "--outdir",
        type=str,
        default="data",
        help="Output directory for data (default: data)",
    )

    args = parser.parse_args()

    # Generate unique run ID
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8]

    # Calculate cutoff date
    since_date = datetime.now() - timedelta(days=args.since_days)

    # Ensure output directories exist
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "raw").mkdir(exist_ok=True)
    (outdir / "summaries").mkdir(exist_ok=True)
    (outdir / "snapshots").mkdir(exist_ok=True)

    # Load source configurations
    sources = load_sources(args.config)

    # Print execution plan
    print("\n" + "=" * 70)
    print("acitrack - Publication Tracker")
    print("=" * 70)
    print(f"Run ID:          {run_id}")
    print(f"Sources:         {len(sources)}")
    print(f"Since:           {since_date.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Config:          {args.config}")
    print(f"Output dir:      {args.outdir}")
    print("=" * 70 + "\n")

    # Phase 1: Fetch publications
    logger.info("Phase 1: Fetching publications")
    publications = fetch_publications(sources, since_date, run_id, str(outdir))
    logger.info("Fetched %d publications", len(publications))

    # Save raw publications to JSON
    if publications:
        raw_output_path = outdir / "raw" / f"{run_id}_publications.json"
        with open(raw_output_path, "w") as f:
            publications_data = [asdict(pub) for pub in publications]
            json.dump(publications_data, f, indent=2)
        logger.info("Saved raw publications to %s", raw_output_path)

    # Phase 2: Summarize publications
    logger.info("Phase 2: Summarizing publications")
    publications = summarize_publications(publications)

    # Phase 3: Detect changes
    logger.info("Phase 3: Detecting changes")
    snapshot_dir = str(outdir / "snapshots")
    changes = detect_changes(publications, snapshot_dir, run_id)
    logger.info(
        "Changes detected - New: %d, Unchanged: %d",
        changes["count_new"],
        changes["count_total"] - changes["count_new"],
    )

    # Save changes output with status
    if publications:
        changes_output_path = outdir / "raw" / f"{run_id}_changes.json"
        changes_output = {
            "run_id": run_id,
            "timestamp": datetime.now().isoformat(),
            "count_new": changes["count_new"],
            "count_total": changes["count_total"],
            "publications": changes["all_with_status"],
        }
        with open(changes_output_path, "w") as f:
            json.dump(changes_output, f, indent=2)
        logger.info("Saved changes output to %s", changes_output_path)

    # Phase 4: Generate report
    logger.info("Phase 4: Generating report")
    generate_report(str(outdir), run_id)

    # Summary
    print("\n" + "=" * 70)
    print("Run Summary")
    print("=" * 70)
    print(f"Publications fetched:    {changes['count_total']}")
    print(f"New publications:        {changes['count_new']}")
    print(f"Unchanged publications:  {changes['count_total'] - changes['count_new']}")
    print("=" * 70 + "\n")

    logger.info("Run completed successfully")


if __name__ == "__main__":
    main()
