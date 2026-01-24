"""Mini-daily tri-model runner.

This script executes an experimental mini-daily run using three models:
1. Claude reviews papers
2. Gemini reviews papers
3. GPT evaluates and produces final decision

Two modes:
A) Default: Fetch publications from RSS feeds (lookback window)
B) --input-csv: Load candidates from a CSV file (e.g., from classic daily run)

Usage:
    # Mode A: Fetch from feeds
    python run_mini_daily.py [--lookback-hours N] [--max-papers N] [--upload-drive]

    # Mode B: Use existing candidates
    python run_mini_daily.py --input-csv data/outputs/daily/daily-YYYY-MM-DD/new.csv [--max-papers N]

Environment variables:
    TRI_MODEL_MINI_DAILY=true (required)
    CLAUDE_API_KEY (at least one reviewer required)
    GEMINI_API_KEY (at least one reviewer required)
    SPOTITEARLY_LLM_API_KEY or OPENAI_API_KEY (required for evaluator)
"""

import argparse
import csv
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config.tri_model_config import (
    validate_config,
    get_available_reviewers,
    MINI_DAILY_WINDOW_HOURS,
    MINI_DAILY_MAX_PAPERS,
)
from tri_model.reviewers import claude_review, gemini_review
from tri_model.evaluator import gpt_evaluate
from ingest.fetch import fetch_publications
from diff.dedupe import deduplicate_publications

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def load_papers_from_csv(csv_path: Path, max_papers: int) -> tuple[list, dict]:
    """Load papers from CSV file (classic daily run output).

    Args:
        csv_path: Path to new.csv file
        max_papers: Maximum papers to load

    Returns:
        (papers_list, metadata_dict)
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    papers = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Convert CSV row to publication dict
            paper = {
                "id": row.get("id", ""),
                "title": row.get("title", ""),
                "source": row.get("source", ""),
                "date": row.get("date", ""),
                "url": row.get("url", ""),
                "one_liner": row.get("one_liner", ""),
                "summary": "",  # Will populate from summaries.json
                "raw_text": "",  # Will populate from summaries.json
            }
            papers.append(paper)

            if len(papers) >= max_papers:
                break

    # Try to load summaries.json from same directory
    summaries_path = csv_path.parent / "summaries.json"
    missing_summary_count = 0

    if summaries_path.exists():
        logger.info("Loading summaries from: %s", summaries_path)
        try:
            with open(summaries_path, 'r', encoding='utf-8') as f:
                summaries_data = json.load(f)

            # Handle both dict and list formats
            summaries_by_id = {}
            if isinstance(summaries_data, dict):
                # Could be {"summaries": [...]} or direct {id: {...}}
                if "summaries" in summaries_data:
                    for item in summaries_data["summaries"]:
                        if "id" in item:
                            summaries_by_id[item["id"]] = item
                else:
                    summaries_by_id = summaries_data
            elif isinstance(summaries_data, list):
                for item in summaries_data:
                    if "id" in item:
                        summaries_by_id[item["id"]] = item

            # Populate summaries
            for paper in papers:
                pub_id = paper["id"]
                if pub_id in summaries_by_id:
                    summary_entry = summaries_by_id[pub_id]
                    # Use summary or one_liner from summaries.json
                    paper["summary"] = summary_entry.get("summary") or summary_entry.get("one_liner") or ""
                    paper["raw_text"] = paper["summary"]  # Use as abstract
                else:
                    # Fallback to one_liner from CSV
                    paper["summary"] = paper.get("one_liner", "")
                    paper["raw_text"] = paper["summary"]
                    missing_summary_count += 1

            logger.info("Populated summaries: %d papers have summaries, %d missing",
                       len(papers) - missing_summary_count, missing_summary_count)

        except Exception as e:
            logger.warning("Failed to load summaries.json: %s (falling back to one_liner)", e)
            # Fallback: use one_liner as summary
            for paper in papers:
                paper["summary"] = paper.get("one_liner", "")
                paper["raw_text"] = paper["summary"]
            missing_summary_count = len(papers)
    else:
        logger.warning("summaries.json not found, using one_liner as fallback")
        # Fallback: use one_liner as summary
        for paper in papers:
            paper["summary"] = paper.get("one_liner", "")
            paper["raw_text"] = paper["summary"]
        missing_summary_count = len(papers)

    metadata = {
        "source_csv": str(csv_path),
        "total_loaded": len(papers),
        "missing_summary_count": missing_summary_count,
    }

    return papers, metadata


def main():
    parser = argparse.ArgumentParser(description="Run mini-daily tri-model experiment")

    # Mode selection
    parser.add_argument("--input-csv", type=Path,
                       help="Path to CSV file with candidates (skips fetch, uses existing papers)")

    # Fetch mode parameters
    parser.add_argument("--lookback-hours", type=int, default=MINI_DAILY_WINDOW_HOURS,
                       help=f"Lookback window in hours (default: {MINI_DAILY_WINDOW_HOURS}, ignored if --input-csv)")
    parser.add_argument("--config", type=str, default="config/sources.yaml",
                       help="Sources configuration file (ignored if --input-csv)")

    # Common parameters
    parser.add_argument("--max-papers", type=int, default=MINI_DAILY_MAX_PAPERS,
                       help=f"Maximum papers to review (default: {MINI_DAILY_MAX_PAPERS})")
    parser.add_argument("--upload-drive", action="store_true",
                       help="Upload outputs to Google Drive")
    parser.add_argument("--outdir", type=Path, default=Path("data"),
                       help="Output directory (default: data)")

    args = parser.parse_args()

    # Validate configuration
    is_valid, error = validate_config()
    if not is_valid:
        logger.error("Configuration validation failed: %s", error)
        sys.exit(1)

    reviewers = get_available_reviewers()
    logger.info("Available reviewers: %s", reviewers)

    if not reviewers:
        logger.error("No reviewers available. Configure CLAUDE_API_KEY or GEMINI_API_KEY")
        sys.exit(1)

    # Determine mode
    mode = "input-csv" if args.input_csv else "lookback"
    now = datetime.now(timezone.utc)
    run_id = f"mini-daily-{now.strftime('%Y-%m-%d-%H%M')}"

    logger.info("=" * 80)
    logger.info("Mini-Daily Tri-Model Run: %s", run_id)
    logger.info("Mode: %s", mode)
    if mode == "input-csv":
        logger.info("Input CSV: %s", args.input_csv)
    else:
        since_date = now - timedelta(hours=args.lookback_hours)
        logger.info("Window: %s to %s (%d hours)",
                    since_date.isoformat(), now.isoformat(), args.lookback_hours)
    logger.info("Max papers: %d", args.max_papers)
    logger.info("=" * 80)

    # Create output directories
    output_dir = args.outdir / "outputs" / "mini-daily" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_dir = args.outdir / "manifests" / "mini-daily"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Output directory: %s", output_dir)

    # Get papers based on mode
    papers_to_review = []
    source_metadata = {}

    if mode == "input-csv":
        # Mode B: Load from CSV
        logger.info("Phase 1: Loading papers from CSV...")
        try:
            papers_to_review, csv_metadata = load_papers_from_csv(args.input_csv, args.max_papers)
            source_metadata = csv_metadata
            logger.info("Loaded %d candidates from CSV, usable: %d",
                       csv_metadata["total_loaded"], len(papers_to_review))
        except Exception as e:
            logger.error("Failed to load CSV: %s", e)
            # Write empty outputs
            _write_empty_outputs(output_dir, manifest_dir, run_id, mode, args, error=str(e))
            sys.exit(1)
    else:
        # Mode A: Fetch from feeds
        logger.info("Phase 1: Fetching publications...")
        since_date = now - timedelta(hours=args.lookback_hours)

        # Load sources
        import yaml
        with open(args.config) as f:
            config = yaml.safe_load(f)
        sources = config.get("sources", [])

        publications, source_stats = fetch_publications(
            sources=sources,
            since_date=since_date,
            run_id=run_id,
            outdir=args.outdir,
        )

        logger.info("Fetched %d publications from %d sources", len(publications), len(source_stats))

        # Phase 2: Deduplicate
        logger.info("Phase 2: Deduplicating...")
        dedupe_result = deduplicate_publications(publications)
        deduped_pubs = dedupe_result["unique_publications"]

        logger.info("After deduplication: %d unique publications", len(deduped_pubs))

        # Phase 3: Select papers for review (limit to max_papers, sorted by date)
        sorted_pubs = sorted(
            deduped_pubs,
            key=lambda p: p.get("date", ""),
            reverse=True
        )
        papers_to_review = sorted_pubs[:args.max_papers]

        source_metadata = {
            "fetched": len(publications),
            "deduplicated": len(deduped_pubs),
            "sources_count": len(source_stats),
        }

    logger.info("Selected %d papers for tri-model review", len(papers_to_review))

    # Check if we have usable papers
    if not papers_to_review:
        logger.warning("No papers to review!")
        _write_empty_outputs(output_dir, manifest_dir, run_id, mode, args,
                            source_metadata=source_metadata,
                            reason="No papers available for review")
        return

    # Phase 4: Tri-model review
    logger.info("Phase 4: Running tri-model reviews...")

    tri_model_reviews = []
    final_decisions = []

    for i, paper in enumerate(papers_to_review, 1):
        logger.info("Processing paper %d/%d: %s", i, len(papers_to_review), paper.get("title", "")[:80])

        # Run reviewers in parallel conceptually (sequential for simplicity)
        claude_result = None
        gemini_result = None

        if 'claude' in reviewers:
            logger.info("  → Claude reviewing...")
            claude_result = claude_review(paper)
            if claude_result["success"]:
                logger.info("  ✓ Claude: score=%d", claude_result["review"]["relevancy_score"])
            else:
                logger.warning("  ✗ Claude failed: %s", claude_result["error"])

        if 'gemini' in reviewers:
            logger.info("  → Gemini reviewing...")
            gemini_result = gemini_review(paper)
            if gemini_result["success"]:
                logger.info("  ✓ Gemini: score=%d", gemini_result["review"]["relevancy_score"])
            else:
                logger.warning("  ✗ Gemini failed: %s", gemini_result["error"])

        # Fallback if both unavailable
        if not claude_result:
            claude_result = {"success": False, "review": None, "error": "Reviewer not configured"}
        if not gemini_result:
            gemini_result = {"success": False, "review": None, "error": "Reviewer not configured"}

        # Evaluate with GPT
        logger.info("  → GPT evaluating...")
        eval_result = gpt_evaluate(paper, claude_result, gemini_result)

        if eval_result["success"]:
            logger.info("  ✓ GPT: final_score=%d, agreement=%s",
                       eval_result["evaluation"]["final_relevancy_score"],
                       eval_result["evaluation"]["agreement_level"])
        else:
            logger.error("  ✗ GPT evaluation failed: %s", eval_result["error"])
            continue  # Skip this paper if evaluation failed

        # Store results
        review_entry = {
            "publication_id": paper.get("id"),
            "title": paper.get("title"),
            "source": paper.get("source"),
            "published_date": paper.get("date"),
            "url": paper.get("url"),
            "claude_review": claude_result,
            "gemini_review": gemini_result,
            "gpt_evaluation": eval_result,
        }
        tri_model_reviews.append(review_entry)

        # Build final decision entry
        evaluation = eval_result["evaluation"]
        final_entry = {
            "id": paper.get("id"),
            "title": paper.get("title"),
            "source": paper.get("source"),
            "published_date": paper.get("date"),
            "url": paper.get("url"),
            "final_relevancy_score": evaluation["final_relevancy_score"],
            "final_relevancy_reason": evaluation["final_relevancy_reason"],
            "final_signals": evaluation["final_signals"],
            "final_summary": evaluation["final_summary"],
            "agreement_level": evaluation["agreement_level"],
            "disagreements": evaluation["disagreements"],
            "evaluator_rationale": evaluation["evaluator_rationale"],
            "confidence": evaluation["confidence"],
            "claude_score": claude_result["review"]["relevancy_score"] if claude_result.get("success") else None,
            "gemini_score": gemini_result["review"]["relevancy_score"] if gemini_result.get("success") else None,
        }
        final_decisions.append(final_entry)

    logger.info("Completed tri-model review: %d papers evaluated", len(final_decisions))

    # Phase 5: Generate must-reads (top 5 by final score)
    logger.info("Phase 5: Generating must-reads...")

    sorted_final = sorted(
        final_decisions,
        key=lambda d: d["final_relevancy_score"],
        reverse=True
    )
    must_reads = sorted_final[:5]

    logger.info("Selected %d must-reads", len(must_reads))

    # Phase 6: Write outputs
    logger.info("Phase 6: Writing outputs...")

    # Write tri_model_reviews.json
    reviews_path = output_dir / "tri_model_reviews.json"
    with open(reviews_path, "w") as f:
        json.dump({
            "run_id": run_id,
            "generated_at": now.isoformat(),
            "mode": mode,
            "reviewers_used": reviewers,
            "total_reviewed": len(tri_model_reviews),
            "reviews": tri_model_reviews,
        }, f, indent=2)
    logger.info("Wrote: %s", reviews_path)

    # Write tri_model_final.json
    final_path = output_dir / "tri_model_final.json"
    with open(final_path, "w") as f:
        json.dump({
            "run_id": run_id,
            "generated_at": now.isoformat(),
            "mode": mode,
            "total_evaluated": len(final_decisions),
            "final_decisions": final_decisions,
        }, f, indent=2)
    logger.info("Wrote: %s", final_path)

    # Write must_reads.json
    must_reads_path = output_dir / "must_reads.json"
    with open(must_reads_path, "w") as f:
        json.dump({
            "run_id": run_id,
            "generated_at": now.isoformat(),
            "mode": mode,
            "total_candidates": len(papers_to_review),
            "must_reads": must_reads,
        }, f, indent=2)
    logger.info("Wrote: %s", must_reads_path)

    # Write simple report.md
    report_path = output_dir / "report.md"
    with open(report_path, "w") as f:
        f.write(f"# Mini-Daily Tri-Model Run: {run_id}\n\n")
        f.write(f"**Generated:** {now.isoformat()}\n\n")
        f.write(f"**Mode:** {mode}\n\n")
        if mode == "input-csv":
            f.write(f"**Input CSV:** {args.input_csv}\n\n")
        f.write(f"**Reviewers Used:** {', '.join(reviewers)}\n\n")
        f.write(f"**Papers Reviewed:** {len(final_decisions)}\n\n")
        f.write(f"**Must-Reads Selected:** {len(must_reads)}\n\n")
        f.write("## Must-Reads\n\n")
        for i, mr in enumerate(must_reads, 1):
            f.write(f"### {i}. {mr['title']}\n\n")
            f.write(f"- **Source:** {mr['source']}\n")
            f.write(f"- **Score:** {mr['final_relevancy_score']}/100\n")
            f.write(f"- **Agreement:** {mr['agreement_level']}\n")
            f.write(f"- **Reason:** {mr['final_relevancy_reason']}\n")
            f.write(f"- **Summary:** {mr['final_summary']}\n")
            f.write(f"- **URL:** {mr['url']}\n\n")
    logger.info("Wrote: %s", report_path)

    # Write manifest
    manifest = {
        "run_id": run_id,
        "run_type": "mini-daily",
        "mode": mode,
        "generated_at": now.isoformat(),
        "counts": {
            "candidates": len(papers_to_review),
            "reviewed": len(final_decisions),
            "must_reads": len(must_reads),
        },
        "reviewers_used": reviewers,
        "local_output_paths": {
            "tri_model_reviews": str(reviews_path.relative_to(args.outdir)),
            "tri_model_final": str(final_path.relative_to(args.outdir)),
            "must_reads": str(must_reads_path.relative_to(args.outdir)),
            "report": str(report_path.relative_to(args.outdir)),
        },
    }

    # Add source-specific metadata
    manifest.update(source_metadata)

    manifest_path = manifest_dir / f"{run_id}.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    logger.info("Wrote: %s", manifest_path)

    # Phase 7: Upload to Drive (if requested)
    if args.upload_drive:
        logger.info("Phase 7: Uploading to Google Drive...")
        try:
            from integrations.drive_upload import get_drive_service, ensure_subfolder, upload_or_update_file

            service = get_drive_service()

            # Get parent folder ID (should be configured in env)
            parent_folder_id = os.getenv("ACITRACK_DRIVE_FOLDER_ID")
            if not parent_folder_id:
                logger.warning("ACITRACK_DRIVE_FOLDER_ID not set, skipping Drive upload")
            else:
                # Create MiniDaily/run_id structure
                mini_daily_folder = ensure_subfolder(service, parent_folder_id, "MiniDaily")
                run_folder = ensure_subfolder(service, mini_daily_folder, run_id)

                # Upload files
                files_to_upload = [
                    ("tri_model_reviews.json", reviews_path),
                    ("tri_model_final.json", final_path),
                    ("must_reads.json", must_reads_path),
                    ("report.md", report_path),
                ]

                drive_paths = {}
                drive_ids = {}

                for filename, local_path in files_to_upload:
                    result = upload_or_update_file(service, run_folder, local_path, filename)
                    if result.get("success"):
                        logger.info("  ✓ Uploaded: %s", filename)
                        drive_paths[filename.replace(".json", "").replace(".md", "")] = f"MiniDaily/{run_id}/{filename}"
                        drive_ids[filename.replace(".json", "").replace(".md", "")] = result.get("file_id")
                    else:
                        logger.warning("  ✗ Failed to upload %s: %s", filename, result.get("error"))

                # Update manifest with Drive info
                manifest["drive_output_paths"] = drive_paths
                manifest["drive_file_ids"] = drive_ids

                # Re-save manifest
                with open(manifest_path, "w") as f:
                    json.dump(manifest, f, indent=2)

                # Upload manifest to Manifests/MiniDaily
                manifests_root = ensure_subfolder(service, parent_folder_id, "Manifests")
                manifests_mini = ensure_subfolder(service, manifests_root, "MiniDaily")
                manifest_result = upload_or_update_file(service, manifests_mini, manifest_path, f"{run_id}.json")

                if manifest_result.get("success"):
                    logger.info("  ✓ Uploaded manifest")

                logger.info("Drive upload complete")

        except Exception as e:
            logger.error("Drive upload failed: %s", e)

    logger.info("=" * 80)
    logger.info("Mini-Daily Tri-Model Run Complete!")
    logger.info("Run ID: %s", run_id)
    logger.info("Mode: %s", mode)
    logger.info("Output Directory: %s", output_dir)
    logger.info("Loaded: %d candidates, Usable: %d, Must-Reads: %d papers",
                len(papers_to_review), len(final_decisions), len(must_reads))
    logger.info("=" * 80)


def _write_empty_outputs(output_dir, manifest_dir, run_id, mode, args, source_metadata=None, error=None, reason=None):
    """Write empty output files when no papers are available."""
    now = datetime.now(timezone.utc)

    # Create directories
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)

    # Write empty must_reads.json
    must_reads_path = output_dir / "must_reads.json"
    with open(must_reads_path, "w") as f:
        json.dump({
            "run_id": run_id,
            "generated_at": now.isoformat(),
            "mode": mode,
            "total_candidates": 0,
            "must_reads": [],
            "reason": reason or error or "No papers available",
        }, f, indent=2)

    # Write empty report.md
    report_path = output_dir / "report.md"
    with open(report_path, "w") as f:
        f.write(f"# Mini-Daily Tri-Model Run: {run_id}\n\n")
        f.write(f"**Generated:** {now.isoformat()}\n\n")
        f.write(f"**Mode:** {mode}\n\n")
        f.write(f"**Status:** No papers available for review\n\n")
        if reason:
            f.write(f"**Reason:** {reason}\n\n")
        if error:
            f.write(f"**Error:** {error}\n\n")

    # Write manifest
    manifest = {
        "run_id": run_id,
        "run_type": "mini-daily",
        "mode": mode,
        "generated_at": now.isoformat(),
        "counts": {
            "candidates": 0,
            "reviewed": 0,
            "must_reads": 0,
        },
        "status": "no_papers",
        "reason": reason or error or "No papers available",
    }

    if source_metadata:
        manifest.update(source_metadata)

    if error:
        manifest["error"] = error

    manifest_path = manifest_dir / f"{run_id}.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    logger.info("Wrote empty outputs to: %s", output_dir)


if __name__ == "__main__":
    main()
