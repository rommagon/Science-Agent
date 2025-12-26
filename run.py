#!/usr/bin/env python3
"""Main CLI entrypoint for acitrack."""

import argparse
import hashlib
import json
import logging
import shutil
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import yaml

from diff.detect_changes import detect_changes
from enrich.commercial import enrich_publication_commercial
from ingest.fetch import fetch_publications
from output.report import export_new_to_csv, generate_report
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


def compute_file_hash(file_path: str) -> str:
    """Compute SHA256 hash of a file.

    Args:
        file_path: Path to file to hash

    Returns:
        Hex string of SHA256 hash
    """
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def create_latest_pointers(run_id: str, outdir: Path) -> None:
    """Create latest_* pointer files by copying run-specific outputs.

    Args:
        run_id: Run identifier for this execution
        outdir: Output directory (data/)
    """
    output_dir = outdir / "output"

    # Define the files to create pointers for
    files_to_copy = [
        (f"{run_id}_report.md", "latest_report.md"),
        (f"{run_id}_new.csv", "latest_new.csv"),
        (f"{run_id}_manifest.json", "latest_manifest.json"),
    ]

    for source_file, dest_file in files_to_copy:
        source_path = output_dir / source_file
        dest_path = output_dir / dest_file

        if source_path.exists():
            shutil.copy2(source_path, dest_path)
            logger.info("Created latest pointer: %s -> %s", source_file, dest_file)
        else:
            logger.warning("Source file not found for latest pointer: %s", source_path)

    print(f"Latest pointers created in {output_dir}")


def generate_manifest(
    run_id: str,
    timestamp: str,
    since_date: str,
    config_path: str,
    active_sources: list[str],
    source_stats: list[dict],
    count_new: int,
    count_total: int,
    outdir: Path,
) -> None:
    """Generate and save run manifest for provenance.

    Args:
        run_id: Unique identifier for this run
        timestamp: ISO format timestamp
        since_date: Since date in YYYY-MM-DD format
        config_path: Path to config file
        active_sources: List of active source names
        source_stats: Per-source statistics from fetch
        count_new: Count of new publications
        count_total: Total count of publications
        outdir: Output directory
    """
    # Compute config file hash
    config_hash = compute_file_hash(config_path)

    # Build manifest
    manifest = {
        "run_id": run_id,
        "timestamp": timestamp,
        "since_date": since_date,
        "config": {
            "path": config_path,
            "sha256": config_hash,
        },
        "active_sources": active_sources,
        "source_details": source_stats,
        "counts": {
            "total_fetched": count_total,
            "total_new": count_new,
            "total_unchanged": count_total - count_new,
        },
        "outputs": {
            "publications_json": f"data/raw/{run_id}_publications.json",
            "changes_json": f"data/raw/{run_id}_changes.json",
            "report_md": f"data/output/{run_id}_report.md",
            "new_csv": f"data/output/{run_id}_new.csv",
            "manifest_json": f"data/output/{run_id}_manifest.json",
            "latest_report_md": "data/output/latest_report.md",
            "latest_new_csv": "data/output/latest_new.csv",
            "latest_manifest_json": "data/output/latest_manifest.json",
        },
    }

    # Save manifest
    output_dir = outdir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / f"{run_id}_manifest.json"

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    logger.info("Saved manifest to %s", manifest_path)
    print(f"Manifest saved: {manifest_path}")


def main() -> None:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="acitrack - Track and summarize cancer research publications",
        epilog="Demo mode: python run.py --reset-snapshot --since-days 7 --max-items-per-source 5"
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=7,
        help="Fetch publications from the last N days (default: 7)",
    )
    parser.add_argument(
        "--since-date",
        type=str,
        help="Fetch publications since this date (YYYY-MM-DD format, overrides --since-days)",
    )
    parser.add_argument(
        "--reset-snapshot",
        action="store_true",
        help="Delete snapshot before running (all items will be marked as NEW)",
    )
    parser.add_argument(
        "--only-sources",
        type=str,
        help="Comma-separated list of source names to include (run only these sources)",
    )
    parser.add_argument(
        "--exclude-sources",
        type=str,
        help="Comma-separated list of source names to exclude (skip these sources)",
    )
    parser.add_argument(
        "--max-items-per-source",
        type=int,
        help="Maximum items to include per source in report/output (still ingests all items)",
    )
    parser.add_argument(
        "--max-new-to-summarize",
        type=int,
        default=200,
        help="Maximum NEW items to summarize (default: 200). Most recent items by date are prioritized.",
    )
    parser.add_argument(
        "--max-new-to-enrich",
        type=int,
        default=500,
        help="Maximum NEW items to enrich with commercial signals (default: 500). Most recent items by date are prioritized.",
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

    # Print demo command hint
    if len(sys.argv) == 1:
        print("\nDemo mode: python run.py --reset-snapshot --since-days 7 --max-items-per-source 5\n")

    # Generate unique run ID
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8]

    # Calculate cutoff date - --since-date overrides --since-days
    if args.since_date:
        try:
            since_date = datetime.strptime(args.since_date, "%Y-%m-%d")
        except ValueError:
            logger.error("Invalid date format for --since-date. Use YYYY-MM-DD format.")
            sys.exit(1)
    else:
        since_date = datetime.now() - timedelta(days=args.since_days)

    # Ensure output directories exist
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "raw").mkdir(exist_ok=True)
    (outdir / "summaries").mkdir(exist_ok=True)
    (outdir / "snapshots").mkdir(exist_ok=True)

    # Handle snapshot reset
    if args.reset_snapshot:
        snapshot_path = outdir / "snapshots" / "latest.json"
        if snapshot_path.exists():
            snapshot_path.unlink()
            print("\n" + "!" * 70)
            print("SNAPSHOT RESET")
            print("!" * 70)
            print("Deleted: data/snapshots/latest.json")
            print("Next run will mark all items as NEW.")
            print("!" * 70 + "\n")
        else:
            print("\n" + "!" * 70)
            print("SNAPSHOT RESET")
            print("!" * 70)
            print("No existing snapshot found.")
            print("Next run will mark all items as NEW.")
            print("!" * 70 + "\n")

    # Load source configurations
    sources = load_sources(args.config)

    # Apply source filtering
    only_sources = None
    exclude_sources = None

    if args.only_sources:
        only_sources = [s.strip() for s in args.only_sources.split(",")]
        sources = [s for s in sources if s.get("name") in only_sources]

    if args.exclude_sources:
        exclude_sources = [s.strip() for s in args.exclude_sources.split(",")]
        sources = [s for s in sources if s.get("name") not in exclude_sources]

    # Get active source names
    active_sources = [s.get("name", "Unknown") for s in sources]

    # Print execution plan
    print("\n" + "=" * 70)
    print("acitrack - Publication Tracker")
    print("=" * 70)
    print(f"Run ID:          {run_id}")
    print(f"Sources:         {len(sources)}")
    print(f"Active sources:  {', '.join(active_sources) if active_sources else 'None'}")
    print(f"Since:           {since_date.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Config:          {args.config}")
    print(f"Output dir:      {args.outdir}")
    if args.max_items_per_source:
        print(f"Max items/src:   {args.max_items_per_source}")
    print("=" * 70 + "\n")

    # Phase 1: Fetch publications
    logger.info("Phase 1: Fetching publications")
    publications, source_stats = fetch_publications(sources, since_date, run_id, str(outdir))
    logger.info("Fetched %d publications", len(publications))

    # Save raw publications to JSON
    if publications:
        raw_output_path = outdir / "raw" / f"{run_id}_publications.json"
        with open(raw_output_path, "w") as f:
            publications_data = [asdict(pub) for pub in publications]
            json.dump(publications_data, f, indent=2)
        logger.info("Saved raw publications to %s", raw_output_path)

    # Phase 2: Detect changes
    logger.info("Phase 2: Detecting changes")
    snapshot_dir = str(outdir / "snapshots")
    changes = detect_changes(publications, snapshot_dir, run_id)
    logger.info(
        "Changes detected - New: %d, Unchanged: %d",
        changes["count_new"],
        changes["count_total"] - changes["count_new"],
    )

    # Phase 3: Summarize NEW publications only
    logger.info("Phase 3: Summarizing NEW publications")
    summary_dir = str(outdir / "summaries")

    # Apply summarization cap: select most recent N items by date
    new_pubs = changes["new"]
    if len(new_pubs) > args.max_new_to_summarize:
        logger.warning(
            "NEW publications (%d) exceed --max-new-to-summarize (%d). "
            "Summarizing only the %d most recent items by date.",
            len(new_pubs),
            args.max_new_to_summarize,
            args.max_new_to_summarize,
        )
        print(f"\n⚠️  WARNING: {len(new_pubs)} NEW items exceed summarization cap of {args.max_new_to_summarize}")
        print(f"   Summarizing only the {args.max_new_to_summarize} most recent items by date.\n")

        # Sort by date (most recent first), then take top N
        sorted_new_pubs = sorted(
            new_pubs,
            key=lambda p: p.date if p.date else "",
            reverse=True
        )
        pubs_to_summarize = sorted_new_pubs[:args.max_new_to_summarize]
        new_pub_ids = {pub.id for pub in pubs_to_summarize}
        skipped_summary_ids = {pub.id for pub in sorted_new_pubs[args.max_new_to_summarize:]}
    else:
        new_pub_ids = {pub.id for pub in new_pubs}
        skipped_summary_ids = set()

    summaries = summarize_publications(publications, new_pub_ids, summary_dir)

    # Add summaries to the all_with_status output
    for pub_dict in changes["all_with_status"]:
        if pub_dict["id"] in summaries:
            pub_dict["essence_bullets"] = summaries[pub_dict["id"]].get("essence_bullets", [])
            pub_dict["one_liner"] = summaries[pub_dict["id"]].get("one_liner", "")
        elif pub_dict["id"] in skipped_summary_ids:
            # Mark skipped items with stub
            pub_dict["essence_bullets"] = []
            pub_dict["one_liner"] = "Summary skipped due to cap."

    # Phase 3.5: Enrich NEW publications with commercial signals
    logger.info("Phase 3.5: Enriching NEW publications with commercial signals")

    # Apply enrichment cap: select most recent N items by date
    new_items_with_status = [p for p in changes["all_with_status"] if p.get("status") == "NEW"]
    if len(new_items_with_status) > args.max_new_to_enrich:
        logger.warning(
            "NEW publications (%d) exceed --max-new-to-enrich (%d). "
            "Enriching only the %d most recent items by date.",
            len(new_items_with_status),
            args.max_new_to_enrich,
            args.max_new_to_enrich,
        )
        print(f"\n⚠️  WARNING: {len(new_items_with_status)} NEW items exceed enrichment cap of {args.max_new_to_enrich}")
        print(f"   Enriching only the {args.max_new_to_enrich} most recent items by date.\n")

        # Sort by date (most recent first), then take top N
        sorted_new_items = sorted(
            new_items_with_status,
            key=lambda p: p.get("date", ""),
            reverse=True
        )
        ids_to_enrich = {p["id"] for p in sorted_new_items[:args.max_new_to_enrich]}
    else:
        ids_to_enrich = {p["id"] for p in new_items_with_status}

    commercial_signals_count = 0
    for pub_dict in changes["all_with_status"]:
        if pub_dict.get("status") == "NEW":
            if pub_dict["id"] in ids_to_enrich:
                # Build combined text from all available fields for thorough scanning
                text_parts = [
                    pub_dict.get("title", ""),
                    pub_dict.get("raw_text", ""),
                    pub_dict.get("one_liner", ""),
                ]
                # Add essence bullets if present
                essence_bullets = pub_dict.get("essence_bullets", [])
                if essence_bullets:
                    text_parts.append("\n".join(essence_bullets))

                combined_text = "\n".join(filter(None, text_parts))

                # Enrich with commercial signals (uses cache if available)
                commercial = enrich_publication_commercial(
                    publication_id=pub_dict["id"],
                    text=combined_text,
                    cache_dir=summary_dir,
                )
                # Add commercial fields to publication
                pub_dict["has_sponsor_signal"] = commercial["has_sponsor_signal"]
                pub_dict["sponsor_names"] = commercial["sponsor_names"]
                pub_dict["company_affiliation_signal"] = commercial["company_affiliation_signal"]
                pub_dict["company_names"] = commercial["company_names"]
                pub_dict["evidence_snippets"] = commercial["evidence_snippets"]

                if commercial["has_sponsor_signal"] or commercial["company_affiliation_signal"]:
                    commercial_signals_count += 1
            else:
                # Skipped due to cap - set default empty values
                pub_dict["has_sponsor_signal"] = False
                pub_dict["sponsor_names"] = []
                pub_dict["company_affiliation_signal"] = False
                pub_dict["company_names"] = []
                pub_dict["evidence_snippets"] = []

    logger.info("Commercial signals detected in %d publications", commercial_signals_count)

    # Save changes output with status, summaries, and commercial signals
    if publications:
        changes_output_path = outdir / "raw" / f"{run_id}_changes.json"
        changes_output = {
            "run_id": run_id,
            "timestamp": datetime.now().isoformat(),
            "since_date": since_date.strftime("%Y-%m-%d"),
            "active_sources": active_sources,
            "max_items_per_source": args.max_items_per_source,
            "count_new": changes["count_new"],
            "count_total": changes["count_total"],
            "publications": changes["all_with_status"],
        }
        with open(changes_output_path, "w") as f:
            json.dump(changes_output, f, indent=2)
        logger.info("Saved changes output to %s", changes_output_path)

    # Phase 4: Generate report
    logger.info("Phase 4: Generating report")
    generate_report(str(outdir), run_id, args.max_items_per_source)

    # Phase 4.5: Export NEW publications to CSV
    logger.info("Phase 4.5: Exporting NEW publications to CSV")
    export_new_to_csv(str(outdir), run_id)

    # Phase 5: Generate manifest
    logger.info("Phase 5: Generating manifest")
    generate_manifest(
        run_id=run_id,
        timestamp=datetime.now().isoformat(),
        since_date=since_date.strftime("%Y-%m-%d"),
        config_path=args.config,
        active_sources=active_sources,
        source_stats=source_stats,
        count_new=changes["count_new"],
        count_total=changes["count_total"],
        outdir=outdir,
    )

    # Phase 6: Create latest pointers
    logger.info("Phase 6: Creating latest pointers")
    create_latest_pointers(run_id, outdir)

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
