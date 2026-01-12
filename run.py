#!/usr/bin/env python3
"""Main CLI entrypoint for acitrack."""
from __future__ import annotations
import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import yaml

from config.daily_config import compute_run_context, get_legacy_run_id, WRITE_SHEETS, RunType
from diff.dedupe import deduplicate_publications
from diff.detect_changes import detect_changes
from enrich.commercial import enrich_publication_commercial
from ingest.fetch import fetch_publications
from output.report import export_new_to_csv, generate_report
from storage.sqlite_store import store_publications, store_run_history
from summarize.summarize import summarize_publications

from config.expansion_config import (
    ENABLE_EXPANSION,
    ENABLE_RELEVANCE_SCORING,
    ENABLE_CREDIBILITY_SCORING,
    STAGE1_TOP_K,
    STAGE2_TOP_M,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def load_sources(config_path: str) -> list[dict]:
    """Load source configurations from YAML file."""
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
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def create_latest_pointers(run_id: str, outdir: Path) -> None:
    """Create latest_* pointer files by copying run-specific outputs."""
    output_dir = outdir / "output"

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
    dedupe_stats: dict = None,
) -> None:
    """Generate and save run manifest for provenance."""
    config_hash = compute_file_hash(config_path)

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

    if dedupe_stats:
        manifest["deduplication"] = {
            "total_fetched_raw": dedupe_stats["total_input"],
            "deduped_total": dedupe_stats["total_output"],
            "duplicates_merged": dedupe_stats["duplicates_merged"],
        }

    output_dir = outdir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / f"{run_id}_manifest.json"

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    logger.info("Saved manifest to %s", manifest_path)
    print(f"Manifest saved: {manifest_path}")


def _default_db_path(outdir: Path) -> str:
    """
    Best-effort DB path resolution:
    - ACITRACK_DB_PATH (if set)
    - {outdir}/db/acitrack.db (default project layout)
    """
    env_path = os.environ.get("ACITRACK_DB_PATH")
    if env_path:
        return env_path
    return str(outdir / "db" / "acitrack.db")


def _to_text_field(value) -> str | None:
    """Convert list/dict to a JSON string; leave strings untouched; None stays None."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value)


def persist_enrichment_to_db(outdir: Path, pubs_with_status: list[dict]) -> None:
    """
    Persist post-ingest enrichment fields to SQLite so they’re queryable even when items
    are UNCHANGED or initially stored as duplicates.

    This fixes the “0 inserted, all duplicates” case where scores/DOIs were computed
    but never written to DB.
    """
    db_path = _default_db_path(outdir)
    if not Path(db_path).exists():
        logger.warning("persist_enrichment_to_db: DB not found at %s (skipping)", db_path)
        return

    # Prepare rows for executemany UPDATE
    rows = []
    for p in pubs_with_status:
        sponsor_flag = int(
            bool(
                p.get("sponsor_flag")
                or p.get("has_sponsor_signal")
                or p.get("company_affiliation_signal")
            )
        )

        rows.append(
            (
                p.get("doi"),                               # doi
                int(p.get("relevance_score") or 0),         # relevance_score
                int(p.get("credibility_score") or 0),       # credibility_score
                p.get("main_interesting_fact"),             # main_interesting_fact
                p.get("relevance_to_spotitearly"),          # relevance_to_spotitearly
                _to_text_field(p.get("modality_tags")),     # modality_tags
                p.get("sample_size"),                       # sample_size
                p.get("study_type"),                        # study_type
                _to_text_field(p.get("key_metrics")),       # key_metrics
                sponsor_flag,                               # sponsor_flag
                p.get("id"),                                # WHERE id=?
            )
        )

    sql = """
    UPDATE publications
    SET
        doi = COALESCE(?, doi),
        relevance_score = ?,
        credibility_score = ?,
        main_interesting_fact = COALESCE(?, main_interesting_fact),
        relevance_to_spotitearly = COALESCE(?, relevance_to_spotitearly),
        modality_tags = COALESCE(?, modality_tags),
        sample_size = COALESCE(?, sample_size),
        study_type = COALESCE(?, study_type),
        key_metrics = COALESCE(?, key_metrics),
        sponsor_flag = COALESCE(?, sponsor_flag)
    WHERE id = ?;
    """

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        # WAL helps concurrent read patterns / long runs
        try:
            cur.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            pass

        cur.executemany(sql, rows)
        conn.commit()
        logger.info(
            "Persisted enrichment to DB: updated %d publications (db=%s)",
            len(rows),
            db_path,
        )
    except Exception as e:
        logger.warning("persist_enrichment_to_db failed: %s (non-blocking)", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main() -> None:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="acitrack - Track and summarize cancer research publications",
        epilog="Demo mode: python run.py --reset-snapshot --since-days 7 --max-items-per-source 5",
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
    parser.add_argument(
        "--upload-drive",
        action="store_true",
        help="Upload latest outputs to Google Drive (requires GOOGLE_APPLICATION_CREDENTIALS and ACITRACK_DRIVE_FOLDER_ID env vars)",
    )
    parser.add_argument(
        "--daily",
        action="store_true",
        help="Run in DAILY mode with date-based run_id (daily-YYYY-MM-DD) and lookback window (default: 48 hours)",
    )
    parser.add_argument(
        "--weekly",
        action="store_true",
        help="Run in WEEKLY mode with week-based run_id (weekly-YYYY-WW) and lookback window (default: 7 days)",
    )
    parser.add_argument(
        "--lookback-hours",
        type=int,
        help="Lookback window in hours for daily runs (default: 48). Overrides --since-days when --daily is used.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        help="Lookback window in days for weekly runs (default: 7). Overrides --since-days when --weekly is used.",
    )
    parser.add_argument(
        "--spreadsheet-id",
        type=str,
        help="Google Sheets spreadsheet ID for Master_Publications and System_Health updates",
    )

    args = parser.parse_args()

    if len(sys.argv) == 1:
        print("\nDemo mode: python run.py --reset-snapshot --since-days 7 --max-items-per-source 5\n")
        print("Daily mode: python run.py --daily --lookback-hours 48\n")
        print("Weekly mode: python run.py --weekly --lookback-days 7\n")

    # Validate mutually exclusive modes
    if args.daily and args.weekly:
        logger.error("Cannot use both --daily and --weekly flags")
        print("\n❌ ERROR: Cannot use both --daily and --weekly flags\n")
        sys.exit(1)

    # Determine run mode and generate run_id
    run_type: RunType = None
    if args.daily:
        run_context = compute_run_context(
            run_type="daily",
            lookback_hours=args.lookback_hours
        )
        run_id = run_context.run_id
        run_type = "daily"
        since_date = run_context.window_start.replace(tzinfo=None)
        run_start_time = run_context.window_end.replace(tzinfo=None)
        logger.info(
            "DAILY MODE: run_id=%s, lookback=%dh, window=%s to %s",
            run_id,
            run_context.lookback_hours,
            run_context.window_start.isoformat(),
            run_context.window_end.isoformat(),
        )
    elif args.weekly:
        run_context = compute_run_context(
            run_type="weekly",
            lookback_days=args.lookback_days
        )
        run_id = run_context.run_id
        run_type = "weekly"
        since_date = run_context.window_start.replace(tzinfo=None)
        run_start_time = run_context.window_end.replace(tzinfo=None)
        logger.info(
            "WEEKLY MODE: run_id=%s, lookback=%dd, window=%s to %s",
            run_id,
            run_context.lookback_days,
            run_context.window_start.isoformat(),
            run_context.window_end.isoformat(),
        )
    else:
        # Legacy mode (no run_type)
        run_type = None
        run_start_time = datetime.now()
        run_id = run_start_time.strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8]

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
    (outdir / "db").mkdir(exist_ok=True)

    # Create run-specific output directories if run_type is set
    if run_type:
        from output.manifest import create_output_directories
        run_output_dir = create_output_directories(run_id, run_type, outdir)
        logger.info("Run output directory: %s", run_output_dir)
    else:
        run_output_dir = outdir / "output"  # Legacy mode

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
    if args.only_sources:
        only_sources = [s.strip() for s in args.only_sources.split(",")]
        sources = [s for s in sources if s.get("name") in only_sources]

    if args.exclude_sources:
        exclude_sources = [s.strip() for s in args.exclude_sources.split(",")]
        sources = [s for s in sources if s.get("name") not in exclude_sources]

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

    # Phase 1.5: Deduplicate across sources
    logger.info("Phase 1.5: Deduplicating publications across sources")
    publications, dedupe_stats = deduplicate_publications(publications)
    logger.info(
        "Deduplication: %d → %d publications (%d duplicates merged)",
        dedupe_stats["total_input"],
        dedupe_stats["total_output"],
        dedupe_stats["duplicates_merged"],
    )

    # Phase 1.6: Store publications to database (additive, non-blocking)
    logger.info("Phase 1.6: Storing publications to database")
    db_result = store_publications(publications, run_id)
    if db_result["success"]:
        logger.info(
            "Database storage: %d inserted, %d duplicates",
            db_result["inserted"],
            db_result["duplicates"],
        )
    else:
        logger.warning("Database storage failed: %s (continuing pipeline)", db_result["error"])

    # Phase 1.7 (optional): Multi-lane expansion  ✅ FIXED: now runs regardless of DB insert result
    if ENABLE_EXPANSION:
        logger.info("Phase 1.7: Multi-lane expansion")
        try:
            from ingest.expand import expand_papers
        except ImportError as e:
            logger.warning("Expansion module not available: %s (skipping expansion)", e)
            logger.info("Phase 1.7: Expansion disabled (module not found)")
        else:
            expanded_pubs, expansion_stats = expand_papers(
                seed_publications=publications,
                run_id=run_id,
                since_date=since_date,
            )

            publications.extend(expanded_pubs)
            logger.info("Expansion: discovered %d new papers", len(expanded_pubs))
            for lane, count in expansion_stats.items():
                logger.info("  - %s: %d papers", lane, count)

            # Re-deduplicate (expansion may introduce duplicates)
            publications, dedupe_stats_post_expansion = deduplicate_publications(publications)
            logger.info(
                "Post-expansion dedup: %d → %d (%d duplicates merged)",
                dedupe_stats_post_expansion["total_input"],
                dedupe_stats_post_expansion["total_output"],
                dedupe_stats_post_expansion["duplicates_merged"],
            )

            # Store expanded publications (in case expansion discovered new IDs not present yet)
            logger.info("Phase 1.7.5: Storing expanded publications to database")
            db_result2 = store_publications(publications, run_id)
            if db_result2["success"]:
                logger.info(
                    "DB after expansion: %d inserted, %d duplicates",
                    db_result2["inserted"],
                    db_result2["duplicates"],
                )
            else:
                logger.warning("DB store after expansion failed: %s (continuing)", db_result2["error"])
    else:
        logger.info("Phase 1.7: Expansion disabled (ENABLE_EXPANSION=false)")

    # Phase 2: Detect changes
    logger.info("Phase 2: Detecting changes")
    snapshot_dir = str(outdir / "snapshots")
    changes = detect_changes(publications, snapshot_dir, run_id)
    logger.info(
        "Changes detected - New: %d, Unchanged: %d",
        changes["count_new"],
        changes["count_total"] - changes["count_new"],
    )

    # Phase 2.5 (optional): Relevance scoring
    if ENABLE_RELEVANCE_SCORING:
        logger.info("Phase 2.5: Computing relevance scores")
        from scoring.relevance import compute_relevance_score

        for pub_dict in changes["all_with_status"]:
            relevance = compute_relevance_score(
                title=pub_dict.get("title", ""),
                abstract=pub_dict.get("raw_text", ""),
                source=pub_dict.get("source", ""),
            )
            pub_dict["relevance_score"] = relevance["score"]
            pub_dict["relevance_reason"] = relevance.get("reason", "")
            pub_dict["matched_keywords"] = relevance.get("matched_keywords", [])

            # Useful default: store "why relevant" as a short string for DB + emails
            if relevance.get("reason"):
                pub_dict["relevance_to_spotitearly"] = relevance.get("reason")
    else:
        for pub_dict in changes["all_with_status"]:
            pub_dict["relevance_score"] = 0

    if ENABLE_RELEVANCE_SCORING:
        logger.info("Relevance scoring complete")

    # Phase 2.6 (optional): Two-stage cost control + credibility scoring
    stage2_pub_ids = set()

    if ENABLE_CREDIBILITY_SCORING and ENABLE_RELEVANCE_SCORING:
        logger.info("Phase 2.6: Two-stage cost control filtering")
        try:
            from scoring.credibility import compute_credibility_score
            from bibliometrics.adapters import enrich_publication
            from diff.dedupe import extract_doi, extract_pmid
        except ImportError as e:
            logger.warning("Credibility scoring modules not available: %s (skipping credibility scoring)", e)
            logger.info("Phase 2.6: Credibility scoring disabled (modules not found)")
        else:
            all_pubs_sorted = sorted(
                changes["all_with_status"],
                key=lambda p: p.get("relevance_score") or 0,  # Treat None as 0 for sorting
                reverse=True,
            )
            stage1_survivors = all_pubs_sorted[:STAGE1_TOP_K]
            logger.info("Stage 1: kept top %d/%d by relevance", len(stage1_survivors), len(all_pubs_sorted))

            stage2_candidates = stage1_survivors[:STAGE2_TOP_M]
            stage2_pub_ids = {pub["id"] for pub in stage2_candidates}

            for pub in stage2_candidates:
                title_text = pub.get("title", "")
                raw_text = pub.get("raw_text", "")
                url = pub.get("url", "")

                from acitrack_types import Publication
                temp_pub = Publication(
                    id=pub["id"],
                    title=title_text,
                    authors=pub.get("authors", []),
                    source=pub.get("source", ""),
                    date=pub.get("date", ""),
                    url=url,
                    raw_text=raw_text,
                    summary="",
                    run_id=run_id,
                )

                doi = extract_doi(temp_pub)
                pmid = extract_pmid(temp_pub)

                biblio_metrics = enrich_publication(
                    doi=doi,
                    pmid=pmid,
                    title=title_text[:200],
                )

                credibility = compute_credibility_score(
                    biblio_metrics=biblio_metrics,
                    title=title_text,
                    abstract=raw_text,
                    has_sponsor_signal=pub.get("has_sponsor_signal", False),
                    sponsor_names=pub.get("sponsor_names", []),
                )

                pub["credibility_score"] = credibility["score"]
                pub["credibility_components"] = credibility.get("components", {})
                pub["credibility_reason"] = credibility.get("reason", "")

                # Persist identifiers when found
                if doi:
                    pub["doi"] = doi
                if biblio_metrics:
                    pub["citation_count"] = biblio_metrics.citation_count
                    pub["citations_per_year"] = biblio_metrics.citations_per_year
                    pub["venue_name"] = biblio_metrics.venue_name
                    pub["pub_type"] = biblio_metrics.pub_type
                    pub["doi"] = biblio_metrics.doi or pub.get("doi")

            logger.info("Stage 2: scored top %d papers with API-backed credibility", len(stage2_candidates))
    else:
        for pub_dict in changes["all_with_status"]:
            pub_dict["credibility_score"] = 0

    # ✅ NEW: persist scores/DOIs/etc even when DB insert was all duplicates
    persist_enrichment_to_db(outdir, changes["all_with_status"])

    # Phase 3: Summarize NEW publications only
    logger.info("Phase 3: Summarizing NEW publications")
    summary_dir = str(outdir / "summaries")

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

        sorted_new_pubs = sorted(
            new_pubs,
            key=lambda p: p.date if p.date else "",
            reverse=True,
        )
        pubs_to_summarize = sorted_new_pubs[:args.max_new_to_summarize]
        new_pub_ids = {pub.id for pub in pubs_to_summarize}
        skipped_summary_ids = {pub.id for pub in sorted_new_pubs[args.max_new_to_summarize:]}
    else:
        new_pub_ids = {pub.id for pub in new_pubs}
        skipped_summary_ids = set()

    summaries = summarize_publications(publications, new_pub_ids, summary_dir)

    for pub_dict in changes["all_with_status"]:
        if pub_dict["id"] in summaries:
            pub_dict["essence_bullets"] = summaries[pub_dict["id"]].get("essence_bullets", [])
            pub_dict["one_liner"] = summaries[pub_dict["id"]].get("one_liner", "")

            # Useful mapping: store one-liner as "main interesting fact" unless already set
            if pub_dict.get("one_liner") and not pub_dict.get("main_interesting_fact"):
                pub_dict["main_interesting_fact"] = pub_dict.get("one_liner")
        elif pub_dict["id"] in skipped_summary_ids:
            pub_dict["essence_bullets"] = []
            pub_dict["one_liner"] = "Summary skipped due to cap."

    # Phase 3.5: Enrich NEW publications with commercial signals
    logger.info("Phase 3.5: Enriching NEW publications with commercial signals")

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

        sorted_new_items = sorted(
            new_items_with_status,
            key=lambda p: p.get("date", ""),
            reverse=True,
        )
        ids_to_enrich = {p["id"] for p in sorted_new_items[:args.max_new_to_enrich]}
    else:
        ids_to_enrich = {p["id"] for p in new_items_with_status}

    commercial_signals_count = 0
    for pub_dict in changes["all_with_status"]:
        if pub_dict.get("status") == "NEW":
            if pub_dict["id"] in ids_to_enrich:
                text_parts = [
                    pub_dict.get("title", ""),
                    pub_dict.get("raw_text", ""),
                    pub_dict.get("one_liner", ""),
                ]
                essence_bullets = pub_dict.get("essence_bullets", [])
                if essence_bullets:
                    text_parts.append("\n".join(essence_bullets))

                combined_text = "\n".join(filter(None, text_parts))

                commercial = enrich_publication_commercial(
                    publication_id=pub_dict["id"],
                    text=combined_text,
                    cache_dir=summary_dir,
                )

                pub_dict["has_sponsor_signal"] = commercial["has_sponsor_signal"]
                pub_dict["sponsor_names"] = commercial["sponsor_names"]
                pub_dict["company_affiliation_signal"] = commercial["company_affiliation_signal"]
                pub_dict["company_names"] = commercial["company_names"]
                pub_dict["evidence_snippets"] = commercial["evidence_snippets"]

                # Keep DB-friendly boolean flag too
                pub_dict["sponsor_flag"] = int(
                    bool(commercial["has_sponsor_signal"] or commercial["company_affiliation_signal"])
                )

                if commercial["has_sponsor_signal"] or commercial["company_affiliation_signal"]:
                    commercial_signals_count += 1
            else:
                pub_dict["has_sponsor_signal"] = False
                pub_dict["sponsor_names"] = []
                pub_dict["company_affiliation_signal"] = False
                pub_dict["company_names"] = []
                pub_dict["evidence_snippets"] = []
                pub_dict["sponsor_flag"] = 0

    logger.info("Commercial signals detected in %d publications", commercial_signals_count)

    # ✅ NEW: persist summary-derived + sponsor flags too
    persist_enrichment_to_db(outdir, changes["all_with_status"])

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

    # Copy report and CSV to run-specific output directory if run_type is set
    if run_type:
        import shutil
        report_src = outdir / "output" / f"{run_id}_report.md"
        csv_src = outdir / "output" / f"{run_id}_new.csv"

        if report_src.exists():
            report_dst = run_output_dir / "report.md"
            shutil.copy2(report_src, report_dst)
            logger.info("Copied report to %s", report_dst)

        if csv_src.exists():
            csv_dst = run_output_dir / "new.csv"
            shutil.copy2(csv_src, csv_dst)
            logger.info("Copied CSV to %s", csv_dst)

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
        dedupe_stats=dedupe_stats,
    )

    # Phase 6: Create latest pointers
    logger.info("Phase 6: Creating latest pointers")
    create_latest_pointers(run_id, outdir)

    # Phase 6.5: Export Drive artifacts (must-reads + summaries + db)
    logger.info("Phase 6.5: Exporting Drive artifacts (must-reads + summaries + db)")
    try:
        from tools.export_must_reads import export_must_reads
        from tools.export_summaries import export_summaries
        from tools.export_db_artifact import export_db_artifact

        # Use run-specific output dir if available, otherwise legacy output dir
        output_subdir = run_output_dir if run_type else (outdir / "output")

        try:
            json_filename = "must_reads.json" if run_type else "latest_must_reads.json"
            md_filename = "must_reads.md" if run_type else "latest_must_reads.md"

            must_reads_result = export_must_reads(
                since_days=30 if run_type != "weekly" else 7,
                limit=20,
                use_ai=True,
                output_dir=output_subdir,
                json_filename=json_filename,
                md_filename=md_filename,
            )
            logger.info(
                "Exported must-reads: %d items (used_ai=%s)",
                must_reads_result["count"],
                must_reads_result["used_ai"],
            )
        except Exception as e:
            logger.warning("Failed to export must-reads (continuing): %s", e)

        try:
            must_reads_path = output_subdir / ("must_reads.json" if run_type else "latest_must_reads.json")
            summaries_path = output_subdir / ("summaries.json" if run_type else "latest_summaries.json")

            summaries_result = export_summaries(
                input_path=must_reads_path,
                output_path=summaries_path,
            )
            logger.info(
                "Exported summaries: %d items (cached=%d, generated=%d)",
                summaries_result["total_count"],
                summaries_result["cached_count"],
                summaries_result["generated_count"],
            )
        except Exception as e:
            logger.warning("Failed to export summaries (continuing): %s", e)

        # Skip DB export for run_type mode (only needed for legacy mode)
        if not run_type:
            try:
                db_result = export_db_artifact(output_path=output_subdir / "latest_db.sqlite.gz")
                if db_result["success"]:
                    logger.info(
                        "Exported database: %.2f MB -> %.2f MB (%.1f%% reduction)",
                        db_result["original_size_mb"],
                        db_result["compressed_size_mb"],
                        db_result["compression_ratio"],
                    )
                else:
                    logger.warning("Database export skipped: %s", db_result.get("error", "Unknown"))
            except Exception as e:
                logger.warning("Failed to export database (continuing): %s", e)

    except Exception as e:
        logger.warning("Phase 6.5 failed (non-blocking): %s", e)

    # Phase 6.7: Generate new manifest (for daily/weekly runs)
    if run_type:
        logger.info("Phase 6.7: Generating run manifest")
        try:
            from output.manifest import generate_run_manifest, save_run_manifest, update_latest_pointer, get_output_paths

            # Get scoring info if available
            scoring_info = {}
            try:
                from mcp_server.llm_relevancy import SCORING_VERSION as REL_VERSION
                from mcp_server.llm_credibility import CREDIBILITY_VERSION as CRED_VERSION
                scoring_info = {
                    "relevancy_version": REL_VERSION,
                    "credibility_version": CRED_VERSION,
                }
            except:
                pass

            manifest = generate_run_manifest(
                run_id=run_id,
                run_type=run_type,
                generated_at=datetime.now().isoformat(),
                window_start=run_context.window_start.isoformat(),
                window_end=run_context.window_end.isoformat(),
                total_candidates=changes["count_total"],
                fetched_count=dedupe_stats["total_input"],
                deduplicated_count=dedupe_stats["total_output"],
                new_count=changes["count_new"],
                unchanged_count=changes["count_total"] - changes["count_new"],
                output_paths=get_output_paths(run_id, run_type, base_dir=args.outdir),
                scoring_info=scoring_info,
                active_sources=active_sources,
                source_stats=source_stats,
                config_path=args.config,
                config_hash=compute_file_hash(args.config),
                dedupe_stats=dedupe_stats,
            )

            save_run_manifest(manifest, outdir)
            update_latest_pointer(manifest, outdir)

            logger.info("Manifest generated and saved")

        except Exception as e:
            logger.warning("Failed to generate manifest (continuing): %s", e)

    # Phase 7 (optional): Upload to Google Drive
    drive_upload_success = True
    if args.upload_drive:
        logger.info("Phase 7: Uploading outputs to Google Drive")

        folder_id = os.environ.get("ACITRACK_DRIVE_FOLDER_ID")
        creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

        if not folder_id:
            logger.error("ACITRACK_DRIVE_FOLDER_ID environment variable not set")
            print("\n❌ ERROR: ACITRACK_DRIVE_FOLDER_ID environment variable not set")
            print("Please set it to your Google Drive folder ID and try again.\n")
            sys.exit(1)

        if not creds_path:
            logger.error("GOOGLE_APPLICATION_CREDENTIALS environment variable not set")
            print("\n❌ ERROR: GOOGLE_APPLICATION_CREDENTIALS environment variable not set")
            print("Please set it to the path of your service account JSON key file.\n")
            sys.exit(1)

        try:
            if run_type:
                # New path: upload to structured folders (Daily/weekly-YYYY-MM-DD/, Weekly/weekly-YYYY-WW/)
                from integrations.drive_upload import upload_run_outputs

                results = upload_run_outputs(
                    parent_folder_id=folder_id,
                    run_id=run_id,
                    run_type=run_type,
                    outdir=outdir,
                )

                if results.get("_has_failures"):
                    drive_upload_success = False
                    logger.error("Some files failed to upload to Google Drive")
                else:
                    logger.info("All files uploaded to Google Drive successfully")

                # Update manifest with Drive paths and file IDs if upload succeeded
                if not results.get("_has_failures") and results.get("drive_output_paths"):
                    logger.info("Updating manifest with Drive paths and file IDs")
                    try:
                        from output.manifest import generate_run_manifest, save_run_manifest, update_latest_pointer, get_output_paths

                        # Get scoring info if available
                        scoring_info = {}
                        try:
                            from mcp_server.llm_relevancy import SCORING_VERSION as REL_VERSION
                            from mcp_server.llm_credibility import CREDIBILITY_VERSION as CRED_VERSION
                            scoring_info = {
                                "relevancy_version": REL_VERSION,
                                "credibility_version": CRED_VERSION,
                            }
                        except:
                            pass

                        # Regenerate manifest with Drive info
                        manifest = generate_run_manifest(
                            run_id=run_id,
                            run_type=run_type,
                            generated_at=datetime.now().isoformat(),
                            window_start=run_context.window_start.isoformat(),
                            window_end=run_context.window_end.isoformat(),
                            total_candidates=changes["count_total"],
                            fetched_count=dedupe_stats["total_input"],
                            deduplicated_count=dedupe_stats["total_output"],
                            new_count=changes["count_new"],
                            unchanged_count=changes["count_total"] - changes["count_new"],
                            output_paths=get_output_paths(run_id, run_type, base_dir=args.outdir),
                            scoring_info=scoring_info,
                            active_sources=active_sources,
                            source_stats=source_stats,
                            config_path=args.config,
                            config_hash=compute_file_hash(args.config),
                            dedupe_stats=dedupe_stats,
                            drive_output_paths=results.get("drive_output_paths"),
                            drive_file_ids=results.get("drive_file_ids"),
                        )

                        # Save updated manifest locally
                        save_run_manifest(manifest, outdir)

                        # Update latest pointer with Drive info
                        update_latest_pointer(manifest, outdir)

                        logger.info("Manifest updated with Drive paths and file IDs")

                        # Re-upload updated manifest to Drive
                        manifest_path = outdir / "manifests" / run_type / f"{run_id}.json"
                        if manifest_path.exists():
                            from integrations.drive_upload import get_drive_service, ensure_subfolder, upload_or_update_file

                            service = get_drive_service()
                            run_type_folder = run_type.capitalize()
                            manifests_root_id = ensure_subfolder(service, folder_id, "Manifests")
                            manifests_type_id = ensure_subfolder(service, manifests_root_id, run_type_folder)

                            upload_or_update_file(service, manifests_type_id, manifest_path, f"{run_id}.json")
                            logger.info("Re-uploaded updated manifest to Drive")

                        # Re-upload updated latest pointer to Drive
                        latest_pointer_path = outdir / "manifests" / run_type / "latest.json"
                        if latest_pointer_path.exists():
                            upload_or_update_file(service, manifests_type_id, latest_pointer_path, "latest.json")
                            logger.info("Re-uploaded updated latest pointer to Drive")

                    except Exception as e:
                        logger.warning("Failed to update manifest with Drive info: %s", e)

            else:
                # Legacy path: upload to root folder
                from integrations.drive_upload import upload_latest_outputs

                results = upload_latest_outputs(folder_id, str(outdir))

                if results.get("_has_failures"):
                    drive_upload_success = False
                    logger.error("Some files failed to upload to Google Drive")
                else:
                    logger.info("All files uploaded to Google Drive successfully")

        except Exception as e:
            logger.error("Google Drive upload failed: %s", e)
            print(f"\n❌ ERROR: Google Drive upload failed: {e}\n")
            drive_upload_success = False

    # Phase 7.5: Store run history (additive, non-blocking)
    logger.info("Phase 7.5: Storing run history to database")

    summarized_count = len(
        [
            p
            for p in changes["all_with_status"]
            if p.get("one_liner") and p.get("one_liner") != "Summary skipped due to cap."
        ]
    )

    run_history_result = store_run_history(
        run_id=run_id,
        started_at=run_start_time.isoformat(),
        since_timestamp=since_date.isoformat(),
        max_items_per_source=args.max_items_per_source if args.max_items_per_source else 0,
        sources_count=len(sources),
        total_fetched=dedupe_stats["total_input"],
        total_deduped=dedupe_stats["total_output"],
        new_count=changes["count_new"],
        unchanged_count=changes["count_total"] - changes["count_new"],
        summarized_count=summarized_count,
        upload_drive=args.upload_drive,
        publications_with_status=changes["all_with_status"],
    )

    if run_history_result["success"]:
        logger.info("Run history stored: %d publications tracked", run_history_result["pub_runs_inserted"])
    else:
        logger.warning("Run history storage failed: %s (continuing)", run_history_result["error"])

    # Phase 8 (optional): Google Sheets integration for daily runs
    sheets_success = True
    if WRITE_SHEETS and args.daily and args.spreadsheet_id:
        logger.info("Phase 8: Updating Google Sheets (Master_Publications and System_Health)")
        logger.info("WRITE_SHEETS=true - Sheets integration enabled")

        try:
            from integrations.sheets import (
                upsert_publications,
                update_system_health,
                verify_run_consistency,
            )

            logger.info("Upserting publications to Master_Publications...")

            def safe_convert_to_dict(item):
                """Convert item to dict, handling dataclasses, dicts, and objects."""
                if hasattr(item, "__dataclass_fields__"):
                    return asdict(item)
                elif isinstance(item, dict):
                    return item
                elif hasattr(item, "__dict__"):
                    return item.__dict__
                else:
                    return None

            publications_dict = []
            converted_count = 0
            skipped_count = 0

            for pub in changes["all_with_status"]:
                result = safe_convert_to_dict(pub)
                if result is not None:
                    publications_dict.append(result)
                    converted_count += 1
                else:
                    logger.warning("Skipped item (unsupported type): %s", type(pub).__name__)
                    skipped_count += 1

            logger.info("Conversion stats: %d converted, %d skipped", converted_count, skipped_count)

            upsert_stats = upsert_publications(
                spreadsheet_id=args.spreadsheet_id,
                publications=publications_dict,
                run_id=run_id,
            )

            logger.info(
                "Publications upserted: %d inserted, %d updated, %d errors",
                upsert_stats["inserted"],
                upsert_stats["updated"],
                upsert_stats["errors"],
            )
            print(f"   Master_Publications: {upsert_stats['inserted']} inserted, {upsert_stats['updated']} updated")

            logger.info("Updating System_Health...")
            health_success = update_system_health(
                spreadsheet_id=args.spreadsheet_id,
                run_id=run_id,
                total_publications_evaluated=changes["count_total"],
                new_this_run=changes["count_new"],
                must_reads_count=0,
                last_error="",
            )

            if health_success:
                logger.info("System_Health updated successfully")
                print("   System_Health: Updated successfully")
            else:
                logger.warning("System_Health update failed (non-blocking)")
                sheets_success = False

            logger.info("Running run-scoped consistency check: CSV vs Sheets (run_id=%s)...", run_id)
            csv_path = outdir / "output" / "latest_new.csv"
            if csv_path.exists():
                consistency = verify_run_consistency(
                    spreadsheet_id=args.spreadsheet_id,
                    run_id=run_id,
                    csv_path=str(csv_path),
                )
                if consistency.get("all_present"):
                    print(
                        f"   ✓ Run consistency check PASSED: All {consistency['csv_count']} CSV records in Sheets "
                        f"(run_id={run_id}, is_new=TRUE)"
                    )
                else:
                    missing_count = len(consistency.get("missing_in_sheets", []))
                    print(f"   ⚠️  Run consistency check: {missing_count}/{consistency['csv_count']} records missing (run_id={run_id})")
                    logger.warning("Run consistency check found %d missing records", missing_count)
            else:
                logger.warning("CSV file not found for consistency check: %s", csv_path)

        except Exception as e:
            logger.error("Google Sheets update failed: %s", e)
            print(f"\n⚠️  WARNING: Google Sheets update failed: {e}")
            sheets_success = False

    elif WRITE_SHEETS and args.daily and not args.spreadsheet_id:
        logger.warning("WRITE_SHEETS=true but --spreadsheet-id not provided, skipping Google Sheets updates")
        print("\n⚠️  WARNING: WRITE_SHEETS=true but --spreadsheet-id not provided, skipping Sheets")
    elif not WRITE_SHEETS and args.daily:
        logger.info("WRITE_SHEETS=false - Skipping Google Sheets integration (feature disabled)")
        print("   Google Sheets: Skipped (WRITE_SHEETS=false)")

    # Summary
    print("\n" + "=" * 70)
    print("Run Summary")
    print("=" * 70)
    print(f"Publications fetched:    {changes['count_total']}")
    print(f"New publications:        {changes['count_new']}")
    print(f"Unchanged publications:  {changes['count_total'] - changes['count_new']}")
    print("=" * 70 + "\n")

    logger.info("Run completed successfully")

    if args.upload_drive and not drive_upload_success:
        logger.error("Exiting with error due to Drive upload failures")
        sys.exit(1)


if __name__ == "__main__":
    main()