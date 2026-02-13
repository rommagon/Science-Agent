#!/usr/bin/env python3
"""Tri-model daily runner - uses classic scraper with Claude + Gemini + GPT evaluation.

This script runs the EXACT SAME ingestion/scraper pipeline as the classic daily run,
but instead of using only GPT for relevancy scoring, it uses:
1. Claude reviewer
2. Gemini reviewer
3. GPT evaluator (compares and synthesizes the two reviews)

Output isolation:
- Run ID format: tri-model-daily-YYYY-MM-DD
- Output directory: data/outputs/tri-model-daily/
- Manifest directory: data/manifests/tri-model-daily/
- Drive folder: TriModelDaily/

This experimental runner does NOT affect the classic daily/weekly pipeline.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import yaml

# Import classic pipeline components (exact same code path)
from config.daily_config import compute_run_context
from diff.dedupe import deduplicate_publications
from ingest.fetch import fetch_publications
from storage.store import get_store, get_database_url

# Get storage implementation (Postgres or SQLite)
store = get_store()
database_url = get_database_url()

# Import tri-model components
from config.tri_model_config import (
    is_tri_model_enabled,
    get_available_reviewers,
    validate_config,
    normalize_validation_result,
)
from config.tri_model_config import RELEVANCY_RUBRIC_VERSION
from tri_model.prompts import get_prompt_hashes
from tri_model.gating import (
    gate_publications,
    filter_for_evaluation,
    load_gating_config,
    get_gating_config_hashes,
    GateResult,
    GatingStats,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def load_sources(config_path: str) -> List[dict]:
    """Load source configurations from YAML file.

    Args:
        config_path: Path to sources.yaml file

    Returns:
        List of source configurations
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


def create_output_directories(run_id: str, outdir: Path) -> Path:
    """Create output directories for tri-model daily run.

    Args:
        run_id: Run identifier (tri-model-daily-YYYY-MM-DD)
        outdir: Base output directory

    Returns:
        Path to run-specific output directory
    """
    # Create base directories
    (outdir / "outputs" / "tri-model-daily").mkdir(parents=True, exist_ok=True)
    (outdir / "manifests" / "tri-model-daily").mkdir(parents=True, exist_ok=True)

    # Create run-specific directory
    run_output_dir = outdir / "outputs" / "tri-model-daily" / run_id
    run_output_dir.mkdir(parents=True, exist_ok=True)

    return run_output_dir


def review_paper_with_tri_model(
    paper: dict,
    available_reviewers: List[str],
) -> Optional[Dict]:
    """Review a single paper using tri-model system.

    Args:
        paper: Paper dictionary with title, source, abstract/raw_text
        available_reviewers: List of available reviewers (claude, gemini)

    Returns:
        Dictionary with review results, or None if all reviewers failed
    """
    from tri_model.reviewers import claude_review, gemini_review
    from tri_model.evaluator import gpt_evaluate
    from tri_model.credibility import score_paper_credibility

    claude_result = None
    gemini_result = None

    # Call Claude reviewer if available
    if "claude" in available_reviewers:
        try:
            claude_result = claude_review(paper)
            if not claude_result.get("success"):
                logger.warning(
                    "Claude review failed for %s: %s",
                    paper.get("id", "unknown")[:16],
                    claude_result.get("error"),
                )
        except Exception as e:
            logger.error("Claude reviewer exception for %s: %s", paper.get("id", "unknown")[:16], e)

    # Call Gemini reviewer if available
    if "gemini" in available_reviewers:
        try:
            gemini_result = gemini_review(paper)
            if not gemini_result.get("success"):
                logger.warning(
                    "Gemini review failed for %s: %s",
                    paper.get("id", "unknown")[:16],
                    gemini_result.get("error"),
                )
        except Exception as e:
            logger.error("Gemini reviewer exception for %s: %s", paper.get("id", "unknown")[:16], e)

    # If both reviewers failed, skip this paper
    if (claude_result is None or not claude_result.get("success")) and \
       (gemini_result is None or not gemini_result.get("success")):
        logger.warning("All reviewers failed for %s, skipping", paper.get("id", "unknown")[:16])
        return None

    # Call GPT evaluator
    try:
        gpt_result = gpt_evaluate(paper, claude_result, gemini_result)
        if not gpt_result.get("success"):
            logger.warning(
                "GPT evaluator failed for %s: %s",
                paper.get("id", "unknown")[:16],
                gpt_result.get("error"),
            )
            return None
    except Exception as e:
        logger.error("GPT evaluator exception for %s: %s", paper.get("id", "unknown")[:16], e)
        return None

    # Score credibility (using same LLM-based system as classic pipeline)
    credibility_result = None
    try:
        credibility_result = score_paper_credibility(paper)
        if credibility_result.get("error"):
            logger.warning(
                "Credibility scoring had issues for %s: %s",
                paper.get("id", "unknown")[:16],
                credibility_result.get("error"),
            )
    except Exception as e:
        logger.error("Credibility scoring exception for %s: %s", paper.get("id", "unknown")[:16], e)
        # Continue even if credibility fails - it's optional enrichment
        credibility_result = {
            "credibility_score": None,
            "credibility_reason": f"Exception: {str(e)}",
            "credibility_confidence": "low",
            "credibility_signals": {},
            "error": str(e)
        }

    # Assemble full result
    return {
        "publication_id": paper.get("id"),
        "title": paper.get("title"),
        "source": paper.get("source"),
        "published_date": paper.get("date"),
        "url": paper.get("url"),
        "claude_review": claude_result,
        "gemini_review": gemini_result,
        "gpt_evaluation": gpt_result,
        "credibility": credibility_result,
    }


def write_must_reads(
    run_id: str,
    results: List[Dict],
    output_dir: Path,
    top_n: int = 5,
) -> Dict:
    """Generate must-reads file from tri-model results.

    Args:
        run_id: Run identifier
        results: List of tri-model review results
        output_dir: Output directory
        top_n: Number of top papers to include

    Returns:
        Must-reads data dictionary
    """
    # Sort by final_relevancy_score descending
    sorted_results = sorted(
        results,
        key=lambda r: r.get("gpt_evaluation", {}).get("evaluation", {}).get("final_relevancy_score", 0),
        reverse=True,
    )

    must_reads = sorted_results[:top_n]

    # Write must_reads.json
    must_reads_data = {
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(),
        "total_candidates": len(results),
        "must_reads_count": len(must_reads),
        "must_reads": [
            {
                "id": paper["publication_id"],
                "title": paper["title"],
                "source": paper["source"],
                "published_date": paper.get("published_date"),
                "url": paper.get("url"),
                "final_relevancy_score": paper["gpt_evaluation"]["evaluation"]["final_relevancy_score"],
                "final_relevancy_reason": paper["gpt_evaluation"]["evaluation"]["final_relevancy_reason"],
                "final_summary": paper["gpt_evaluation"]["evaluation"]["final_summary"],
                "agreement_level": paper["gpt_evaluation"]["evaluation"]["agreement_level"],
                "confidence": paper["gpt_evaluation"]["evaluation"]["confidence"],
                "claude_score": paper["claude_review"]["review"]["relevancy_score"] if paper["claude_review"] and paper["claude_review"].get("success") else None,
                "gemini_score": paper["gemini_review"]["review"]["relevancy_score"] if paper["gemini_review"] and paper["gemini_review"].get("success") else None,
                # Credibility fields (from classic pipeline credibility system)
                "credibility_score": paper.get("credibility", {}).get("credibility_score"),
                "credibility_reason": paper.get("credibility", {}).get("credibility_reason", ""),
                "credibility_confidence": paper.get("credibility", {}).get("credibility_confidence", "low"),
                "credibility_signals": paper.get("credibility", {}).get("credibility_signals", {}),
                # Gate info (if gating was enabled)
                **({
                    "gate_bucket": paper["gate_info"]["gate_bucket"],
                    "gate_score": paper["gate_info"]["gate_score"],
                    "gate_reason": paper["gate_info"]["gate_reason"],
                    "gate_audit_selected": paper["gate_info"]["gate_audit_selected"],
                } if paper.get("gate_info") else {}),
            }
            for paper in must_reads
        ],
    }

    must_reads_path = output_dir / "must_reads.json"
    with open(must_reads_path, "w", encoding="utf-8") as f:
        json.dump(must_reads_data, f, indent=2, ensure_ascii=False)

    logger.info("Wrote must-reads to %s", must_reads_path)

    return must_reads_data


def write_report(
    run_id: str,
    must_reads_data: Dict,
    output_dir: Path,
    window_start: datetime,
    window_end: datetime,
) -> None:
    """Write human-readable markdown report.

    Args:
        run_id: Run identifier
        must_reads_data: Must-reads data dictionary
        output_dir: Output directory
        window_start: Window start time
        window_end: Window end time
    """
    report_path = output_dir / "report.md"

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# Tri-Model Daily Run: {run_id}\n\n")
        f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"**Window:** {window_start.strftime('%Y-%m-%d %H:%M')} to {window_end.strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(f"**Total Candidates:** {must_reads_data['total_candidates']}\n\n")
        f.write(f"**Must-Reads:** {must_reads_data['must_reads_count']}\n\n")

        f.write("---\n\n")
        f.write("## Top Must-Read Publications\n\n")

        for i, paper in enumerate(must_reads_data["must_reads"], 1):
            f.write(f"### {i}. {paper['title']}\n\n")
            f.write(f"**Source:** {paper['source']}\n\n")
            f.write(f"**Published:** {paper.get('published_date', 'Unknown')}\n\n")
            f.write(f"**Final Score:** {paper['final_relevancy_score']}/100 (Confidence: {paper['confidence']})\n\n")

            if paper.get("claude_score") is not None and paper.get("gemini_score") is not None:
                f.write(f"**Individual Scores:** Claude: {paper['claude_score']}, Gemini: {paper['gemini_score']}\n\n")

            f.write(f"**Agreement:** {paper['agreement_level']}\n\n")
            f.write(f"**Summary:** {paper['final_summary']}\n\n")
            f.write(f"**Why Relevant:** {paper['final_relevancy_reason']}\n\n")
            f.write("---\n\n")

    logger.info("Wrote report to %s", report_path)


def write_manifest(
    run_id: str,
    output_dir: Path,
    window_start: datetime,
    window_end: datetime,
    raw_fetched_count: int,
    window_filtered_count: int,
    deduplicated_count: int,
    usable_count: int,
    missing_abstract_count: int,
    reviewer_failures_count: int,
    gpt_eval_count: int,
    available_reviewers: List[str],
    window_mode: str,
    matched_daily_run_id: Optional[str] = None,
    prompt_version: str = "v2",
    rubric_version: str = RELEVANCY_RUBRIC_VERSION,
    prompt_hash: Optional[str] = None,
    experiment_id: Optional[str] = None,
    gating_enabled: bool = False,
    gating_stats: Optional[Dict] = None,
    gating_config_hashes: Optional[Dict] = None,
    gate_audit_rate: float = 0.02,
) -> None:
    """Write manifest file with run metadata.

    Args:
        run_id: Run identifier
        output_dir: Output directory
        window_start: Window start time
        window_end: Window end time
        raw_fetched_count: Raw candidates fetched (before window filter)
        window_filtered_count: Candidates after window filter
        deduplicated_count: Candidates after deduplication
        usable_count: Candidates with usable abstracts
        missing_abstract_count: Candidates missing abstracts
        reviewer_failures_count: Number of reviewer failures
        gpt_eval_count: Number of successful GPT evaluations
        available_reviewers: List of available reviewers
        window_mode: Window determination mode
        matched_daily_run_id: Matched daily run ID (if applicable)
        gating_enabled: Whether gating was enabled for this run
        gating_stats: Gating statistics (bucket counts, etc.)
        gating_config_hashes: Hashes of venue/keyword lists
        gate_audit_rate: Audit rate used for gating
    """
    manifest_data = {
        "run_id": run_id,
        "run_type": "tri-model-daily",
        "mode": "tri-model-daily",
        "generated_at": datetime.now().isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "window_mode": window_mode,
        "counts": {
            "raw_fetched": raw_fetched_count,
            "window_filtered": window_filtered_count,
            "deduplicated": deduplicated_count,
            "usable": usable_count,
            "missing_abstract": missing_abstract_count,
            "reviewer_failures": reviewer_failures_count,
            "gpt_evaluations": gpt_eval_count,
        },
        "reviewers_used": available_reviewers,
        "prompt_version": prompt_version,
        "rubric_version": rubric_version,
        "prompt_hash": prompt_hash,
        "local_output_paths": {
            "tri_model_events": str(output_dir / "tri_model_events.jsonl"),
            "must_reads": str(output_dir / "must_reads.json"),
            "report": str(output_dir / "report.md"),
            "manifest": str(output_dir / "manifest.json"),
        },
    }

    # Add gating information
    manifest_data["gating"] = {
        "enabled": gating_enabled,
        "audit_rate": gate_audit_rate,
    }
    if gating_enabled and gating_stats:
        manifest_data["gating"]["stats"] = gating_stats
    if gating_enabled and gating_config_hashes:
        manifest_data["gating"]["config_hashes"] = gating_config_hashes

    # Add matched_daily_run_id if applicable
    if matched_daily_run_id:
        manifest_data["matched_daily_run_id"] = matched_daily_run_id
    if experiment_id:
        manifest_data["experiment_id"] = experiment_id

    # Write to run output directory
    manifest_path = output_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)

    # Also write to manifests directory
    manifests_dir = output_dir.parent.parent.parent / "manifests" / "tri-model-daily"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    manifest_copy_path = manifests_dir / f"{run_id}.json"
    with open(manifest_copy_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)

    logger.info("Wrote manifest to %s and %s", manifest_path, manifest_copy_path)


def main() -> None:
    """Main entrypoint for tri-model daily runner."""
    parser = argparse.ArgumentParser(
        description="Tri-model daily runner - uses classic scraper with Claude + Gemini + GPT",
    )
    parser.add_argument(
        "--run-date",
        type=str,
        help="Run date (YYYY-MM-DD format, defaults to today)",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        help="Explicit run ID override (optional)",
    )
    parser.add_argument(
        "--experiment-id",
        type=str,
        help="Optional experiment identifier for run tracking",
    )
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=48,
        help="Lookback window in hours (default: 48, matches classic daily)",
    )
    parser.add_argument(
        "--prompt-version",
        type=str,
        choices=["v1", "v2", "v3"],
        default="v3",
        help="Prompt version for tri-model reviewers/evaluator (default: v3)",
    )
    parser.add_argument(
        "--match-daily-run",
        type=str,
        help="Match exact window from classic daily run (e.g., daily-2026-01-12). Loads window_start/window_end from manifest.",
    )
    parser.add_argument(
        "--window-start",
        type=str,
        help="Explicit window start timestamp (ISO8601 format, e.g., 2026-01-10T21:31:13+00:00). Overrides --run-date.",
    )
    parser.add_argument(
        "--window-end",
        type=str,
        help="Explicit window end timestamp (ISO8601 format, e.g., 2026-01-12T21:31:13+00:00). Overrides --run-date.",
    )
    parser.add_argument(
        "--max-papers",
        type=int,
        help="Maximum papers to review (optional cap)",
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
        help="Upload outputs to Google Drive (requires GOOGLE_APPLICATION_CREDENTIALS and ACITRACK_DRIVE_FOLDER_ID env vars)",
    )
    parser.add_argument(
        "--ingest-backend",
        action="store_true",
        help="Ingest outputs to Postgres-backed backend (requires BACKEND_URL and BACKEND_API_KEY env vars)",
    )
    parser.add_argument(
        "--backend-url",
        type=str,
        help="Backend URL override (default: from BACKEND_URL env var)",
    )
    parser.add_argument(
        "--backend-api-key",
        type=str,
        help="Backend API key override (default: from BACKEND_API_KEY env var)",
    )
    parser.add_argument(
        "--ingest-chunk-size",
        type=int,
        default=100,
        help="Backend ingestion chunk size for tri-model events (default: 100)",
    )
    parser.add_argument(
        "--ingest-strict",
        action="store_true",
        help="Exit with non-zero code if backend ingestion fails",
    )

    # Gating arguments (two-stage pipeline)
    parser.add_argument(
        "--enable-gating",
        action="store_true",
        default=True,
        help="Enable two-stage gating to reduce tri-model evaluations (default: True)",
    )
    parser.add_argument(
        "--disable-gating",
        action="store_true",
        help="Disable gating and evaluate all papers with tri-model",
    )
    parser.add_argument(
        "--gate-audit-rate",
        type=float,
        default=0.02,
        help="Fraction of LOW bucket papers to audit with tri-model (default: 0.02 = 2%%)",
    )
    parser.add_argument(
        "--gate-venue-whitelist",
        type=str,
        help="Path to venue whitelist file (json/yaml/txt). Uses built-in list if not specified.",
    )
    parser.add_argument(
        "--gate-keywords",
        type=str,
        help="Path to keywords file (json/yaml/txt). Uses built-in list if not specified.",
    )

    args = parser.parse_args()

    # Handle gating enable/disable flags
    args.gating_enabled = args.enable_gating and not args.disable_gating
    os.environ["TRI_MODEL_PROMPT_VERSION"] = args.prompt_version

    # Validate tri-model configuration
    if not is_tri_model_enabled():
        logger.error("Tri-model system is not enabled. Set TRI_MODEL_MINI_DAILY=true")
        print("\n❌ ERROR: Tri-model system is not enabled.")
        print("   Set environment variable: TRI_MODEL_MINI_DAILY=true\n")
        sys.exit(1)

    # Validate configuration (with backwards-compatible normalization)
    raw_validation_result = validate_config()
    validation_result = normalize_validation_result(raw_validation_result)

    if not validation_result["valid"]:
        # Log validation errors (without exposing secrets)
        error_summary = validation_result.get("details") or "Configuration validation failed"
        logger.info("Tri-model validation failed: %s", error_summary)

        print("\n❌ ERROR: Configuration validation failed:")
        for error in validation_result["errors"]:
            # Never print API keys or secrets
            safe_error = error.replace(os.getenv("CLAUDE_API_KEY", ""), "***") if os.getenv("CLAUDE_API_KEY") else error
            safe_error = safe_error.replace(os.getenv("GEMINI_API_KEY", ""), "***") if os.getenv("GEMINI_API_KEY") else safe_error
            safe_error = safe_error.replace(os.getenv("SPOTITEARLY_LLM_API_KEY", ""), "***") if os.getenv("SPOTITEARLY_LLM_API_KEY") else safe_error
            print(f"   - {safe_error}")
        print()
        sys.exit(1)

    available_reviewers = get_available_reviewers()
    logger.info("Available reviewers: %s", available_reviewers)

    # Determine window based on priority: --match-daily-run > explicit windows > --run-date
    matched_daily_run_id = None
    window_mode = None

    if args.match_daily_run:
        # Mode 1: Match exact window from classic daily run
        matched_daily_run_id = args.match_daily_run
        manifest_path = Path(args.outdir) / "manifests" / "daily" / f"{matched_daily_run_id}.json"

        if not manifest_path.exists():
            logger.error("Daily manifest not found: %s", manifest_path)
            print(f"\n❌ ERROR: Daily manifest not found: {manifest_path}")
            print("   Make sure the daily run has completed and manifest exists.\n")
            sys.exit(1)

        with open(manifest_path, "r") as f:
            daily_manifest = json.load(f)

        # Parse window timestamps from daily manifest
        window_start_str = daily_manifest.get("window_start")
        window_end_str = daily_manifest.get("window_end")

        if not window_start_str or not window_end_str:
            logger.error("Daily manifest missing window_start or window_end")
            print(f"\n❌ ERROR: Daily manifest missing window timestamps\n")
            sys.exit(1)

        # Parse ISO8601 timestamps (handle both with and without timezone)
        try:
            window_start = datetime.fromisoformat(window_start_str.replace("+00:00", "").replace("Z", ""))
            window_end = datetime.fromisoformat(window_end_str.replace("+00:00", "").replace("Z", ""))
        except ValueError as e:
            logger.error("Failed to parse window timestamps: %s", e)
            print(f"\n❌ ERROR: Failed to parse window timestamps from daily manifest\n")
            sys.exit(1)

        since_date = window_start
        window_mode = "matched_daily"

        # Generate run_id with suffix to indicate match
        run_date_str = window_end.strftime("%Y-%m-%d")
        run_id = f"tri-model-daily-{run_date_str}_match-{matched_daily_run_id}"

        logger.info("Matching classic daily run: %s", matched_daily_run_id)
        logger.info("Window from manifest: %s to %s", window_start.isoformat(), window_end.isoformat())

    elif args.window_start and args.window_end:
        # Mode 2: Explicit window timestamps
        try:
            window_start = datetime.fromisoformat(args.window_start.replace("+00:00", "").replace("Z", ""))
            window_end = datetime.fromisoformat(args.window_end.replace("+00:00", "").replace("Z", ""))
        except ValueError as e:
            logger.error("Invalid window timestamp format: %s", e)
            print(f"\n❌ ERROR: Invalid window timestamp format. Use ISO8601 (e.g., 2026-01-10T21:31:13)\n")
            sys.exit(1)

        since_date = window_start
        window_mode = "explicit"

        # Generate run_id from window_end date
        run_date_str = window_end.strftime("%Y-%m-%d")
        run_id = f"tri-model-daily-{run_date_str}_explicit"

        logger.info("Using explicit window: %s to %s", window_start.isoformat(), window_end.isoformat())

    else:
        # Mode 3: Default midnight-anchored window from --run-date
        if args.run_date:
            try:
                run_date = datetime.strptime(args.run_date, "%Y-%m-%d")
            except ValueError:
                logger.error("Invalid date format for --run-date. Use YYYY-MM-DD format.")
                sys.exit(1)
        else:
            run_date = datetime.now()

        run_id = f"tri-model-daily-{run_date.strftime('%Y-%m-%d')}"

        # Compute window: end at current time, look back N hours
        window_end = run_date
        window_start = window_end - timedelta(hours=args.lookback_hours)
        since_date = window_start
        window_mode = "rolling"

        logger.info("Using rolling window (end=now, lookback=%dh)", args.lookback_hours)

    logger.info(
        "TRI-MODEL DAILY MODE: run_id=%s, lookback=%dh, window=%s to %s",
        run_id,
        args.lookback_hours,
        window_start.isoformat(),
        window_end.isoformat(),
    )

    if args.run_id:
        run_id = args.run_id
        logger.info("Overriding run_id with explicit value: %s", run_id)

    # Create output directories
    outdir = Path(args.outdir)
    run_output_dir = create_output_directories(run_id, outdir)
    logger.info("Output directory: %s", run_output_dir)

    # Print execution plan
    print("\n" + "=" * 70)
    print("Tri-Model Daily Run - Classic Scraper Path")
    print("=" * 70)
    print(f"Run ID:          {run_id}")
    print(f"Prompt version:  {args.prompt_version}")
    print(f"Window:          {window_start.strftime('%Y-%m-%d %H:%M:%S')} to {window_end.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Window mode:     {window_mode}")
    if matched_daily_run_id:
        print(f"Matched run:     {matched_daily_run_id}")
    print(f"Reviewers:       {', '.join(available_reviewers)}")
    print(f"Output dir:      {run_output_dir}")
    if args.max_papers:
        print(f"Max papers:      {args.max_papers}")
    print("=" * 70 + "\n")

    # Phase 1: Fetch publications using CLASSIC scraper path
    logger.info("Phase 1: Fetching publications (classic scraper path)")
    sources = load_sources(args.config)
    publications, source_stats = fetch_publications(sources, since_date, run_id, str(outdir))
    raw_fetched_count = len(publications)
    logger.info("Fetched %d publications from classic scraper", raw_fetched_count)

    # Phase 1.4: Apply window_end filter (tri-model only)
    # Classic scraper uses since_date but no upper bound (uses NOW).
    # For historical parity, filter to window_end.
    logger.info("Phase 1.4: Filtering publications to window [%s, %s]", window_start.isoformat(), window_end.isoformat())

    filtered_publications = []
    missing_date_count = 0
    outside_window_count = 0

    for pub in publications:
        # Get published date
        pub_date = getattr(pub, "date", None)

        if pub_date is None:
            # No date available - exclude from window filter
            missing_date_count += 1
            continue

        # Parse date if it's a string
        if isinstance(pub_date, str):
            try:
                # Handle various ISO8601 formats
                pub_date = datetime.fromisoformat(pub_date.replace("+00:00", "").replace("Z", ""))
            except (ValueError, AttributeError):
                missing_date_count += 1
                continue

        # Apply window filter: window_start <= pub_date <= window_end
        if pub_date < window_start or pub_date > window_end:
            outside_window_count += 1
            continue

        filtered_publications.append(pub)

    publications = filtered_publications
    window_filtered_count = len(publications)

    logger.info(
        "Window filter: %d raw → %d filtered (%d outside window, %d missing date)",
        raw_fetched_count,
        window_filtered_count,
        outside_window_count,
        missing_date_count,
    )

    # Phase 1.5: Deduplicate (classic dedupe logic)
    logger.info("Phase 1.5: Deduplicating publications")
    publications, dedupe_stats = deduplicate_publications(publications)
    logger.info(
        "Deduplication: %d → %d publications (%d duplicates merged)",
        dedupe_stats["total_input"],
        dedupe_stats["total_output"],
        dedupe_stats["duplicates_merged"],
    )

    # Phase 1.6: Store publications to database
    logger.info("Phase 1.6: Storing publications to database")
    db_path = str(outdir / "db" / "acitrack.db")
    if database_url:
        db_result = store.store_publications(publications, run_id, database_url)
    else:
        db_result = store.store_publications(publications, run_id, db_path)
    if db_result["success"]:
        logger.info(
            "Database storage: %d inserted, %d duplicates",
            db_result["inserted"],
            db_result["duplicates"],
        )
    else:
        logger.warning("Database storage failed: %s (continuing pipeline)", db_result["error"])

    # Phase 1.7: Enrich with canonical URLs
    logger.info("Phase 1.7: Enriching publications with canonical URLs")
    try:
        from enrich.canonical_url import resolve_canonical_url
        canonical_url_success = 0
        canonical_url_failed = 0

        for pub in publications:
            try:
                pub_dict = {
                    "id": pub.id,
                    "title": pub.title,
                    "url": getattr(pub, "url", ""),
                    "doi": None,  # Will be extracted
                    "pmid": None,  # Will be extracted
                    "source": pub.source,
                    "raw_text": getattr(pub, "raw_text", ""),
                }

                canonical_url, doi, pmid, source_type = resolve_canonical_url(pub_dict)

                if canonical_url or doi or pmid or source_type:
                    if database_url:
                        result = store.update_publication_canonical_url(
                            publication_id=pub.id,
                            canonical_url=canonical_url,
                            doi=doi,
                            pmid=pmid,
                            source_type=source_type,
                            database_url=database_url,
                        )
                    else:
                        result = store.update_publication_canonical_url(
                            publication_id=pub.id,
                            canonical_url=canonical_url,
                            doi=doi,
                            pmid=pmid,
                            source_type=source_type,
                            db_path=db_path,
                        )

                    if result.get("success"):
                        canonical_url_success += 1
                    else:
                        canonical_url_failed += 1
            except Exception as e:
                canonical_url_failed += 1
                logger.debug("Failed to resolve canonical URL for %s: %s", pub.id[:16], e)

        logger.info(
            "Canonical URL enrichment: %d success, %d failed",
            canonical_url_success,
            canonical_url_failed,
        )
    except ImportError:
        logger.warning("Canonical URL module not available, skipping enrichment")
    except Exception as e:
        logger.warning("Canonical URL enrichment failed: %s (continuing pipeline)", e)

    # Phase 1.8: Generate embeddings (non-blocking)
    logger.info("Phase 1.8: Generating embeddings for publications")
    try:
        from acitrack.semantic_search import (
            build_embedding_text,
            compute_content_hash,
            embed_text,
            embedding_to_bytes,
            get_embedding_dimension,
            get_openai_api_key,
            DEFAULT_EMBEDDING_MODEL,
        )

        api_key = get_openai_api_key()
        if not api_key:
            logger.warning("OpenAI API key not configured, skipping embedding generation")
        else:
            embedding_model = DEFAULT_EMBEDDING_MODEL
            embedding_dim = get_embedding_dimension(embedding_model)
            embeddings_success = 0
            embeddings_failed = 0

            for pub in publications:
                try:
                    pub_dict = {
                        "title": pub.title,
                        "raw_text": getattr(pub, "raw_text", ""),
                        "summary": getattr(pub, "summary", ""),
                        "source": pub.source,
                        "venue": getattr(pub, "venue", ""),
                        "published_date": getattr(pub, "date", ""),
                    }

                    text = build_embedding_text(pub_dict)
                    if not text or len(text.strip()) < 10:
                        continue

                    content_hash = compute_content_hash(text)
                    embedding = embed_text(text, model=embedding_model, api_key=api_key)

                    if embedding is not None:
                        embedding_bytes = embedding_to_bytes(embedding)

                        if database_url:
                            result = store.store_publication_embedding(
                                publication_id=pub.id,
                                embedding_model=embedding_model,
                                embedding_dim=embedding_dim,
                                embedding=embedding_bytes,
                                content_hash=content_hash,
                                database_url=database_url,
                            )
                        else:
                            result = store.store_publication_embedding(
                                publication_id=pub.id,
                                embedding_model=embedding_model,
                                embedding_dim=embedding_dim,
                                embedding=embedding_bytes,
                                content_hash=content_hash,
                                db_path=db_path,
                            )

                        if result.get("success"):
                            embeddings_success += 1
                        else:
                            embeddings_failed += 1
                    else:
                        embeddings_failed += 1
                except Exception as e:
                    embeddings_failed += 1
                    logger.debug("Failed to generate embedding for %s: %s", pub.id[:16], e)

            logger.info(
                "Embedding generation: %d success, %d failed",
                embeddings_success,
                embeddings_failed,
            )
    except ImportError as e:
        logger.warning("Semantic search module not available, skipping embedding generation: %s", e)
    except Exception as e:
        logger.warning("Embedding generation failed: %s (continuing pipeline)", e)

    # Convert publications to dict format for tri-model review
    all_papers = []
    missing_abstract_count = 0

    for pub in publications:
        paper = {
            "id": pub.id,
            "title": pub.title,
            "source": pub.source,
            "venue": getattr(pub, "venue", None) or pub.source,
            "date": getattr(pub, "date", None),
            "url": getattr(pub, "url", None),
            "raw_text": getattr(pub, "raw_text", ""),
        }

        if not paper["raw_text"]:
            missing_abstract_count += 1
            logger.debug("Missing abstract for %s", pub.id[:16])
        else:
            all_papers.append(paper)

    # Phase 1.9: Two-stage gating (if enabled)
    gating_stats = None
    gating_config_hashes = None
    paper_gate_results = {}  # Map paper ID to GateResult

    if args.gating_enabled:
        logger.info("Phase 1.9: Two-stage gating (%d papers)", len(all_papers))

        # Load gating configuration
        venue_whitelist, keywords = load_gating_config(
            venue_whitelist_path=args.gate_venue_whitelist,
            keywords_path=args.gate_keywords,
        )

        # Get config hashes for manifest
        gating_config_hashes = get_gating_config_hashes(venue_whitelist, keywords)

        # Gate all papers
        gated_results, gating_stats_obj = gate_publications(
            publications=all_papers,
            venue_whitelist=venue_whitelist,
            keywords=keywords,
            audit_rate=args.gate_audit_rate,
            audit_seed=hash(run_id) % (2**31),  # Deterministic seed from run_id
        )

        gating_stats = gating_stats_obj.to_dict()

        # Store gate results for each paper
        for paper, gate_result in gated_results:
            paper_gate_results[paper["id"]] = gate_result

        # Filter to papers that should be tri-model evaluated
        papers_to_evaluate = filter_for_evaluation(gated_results)
        papers_to_review = [paper for paper, _ in papers_to_evaluate]

        logger.info(
            "Gating: %d usable → %d high + %d maybe + %d audited_low = %d to evaluate",
            len(all_papers),
            gating_stats["high"],
            gating_stats["maybe"],
            gating_stats["audited_low"],
            len(papers_to_review),
        )

        # Print gating summary
        print(f"\n{'='*70}")
        print("Gating Summary")
        print(f"{'='*70}")
        print(f"  Total usable papers:    {len(all_papers)}")
        print(f"  HIGH bucket:            {gating_stats['high']}")
        print(f"  MAYBE bucket:           {gating_stats['maybe']}")
        print(f"  LOW bucket:             {gating_stats['low']}")
        print(f"  LOW audited:            {gating_stats['audited_low']} ({args.gate_audit_rate*100:.1f}% audit rate)")
        print(f"  To tri-model evaluate:  {len(papers_to_review)}")
        print(f"  Evaluation reduction:   {100*(1 - len(papers_to_review)/len(all_papers)):.1f}%")
        print(f"{'='*70}\n")
    else:
        logger.info("Gating disabled, all %d papers will be tri-model evaluated", len(all_papers))
        papers_to_review = all_papers

    # Apply max-papers cap if specified (applies after gating)
    if args.max_papers and len(papers_to_review) > args.max_papers:
        logger.info("Applying max-papers cap: %d → %d", len(papers_to_review), args.max_papers)
        # Sort by date descending and take most recent
        papers_to_review.sort(key=lambda p: p.get("date", ""), reverse=True)
        papers_to_review = papers_to_review[:args.max_papers]

    logger.info(
        "Candidates: %d total, %d usable, %d missing abstracts",
        len(publications),
        len(papers_to_review),
        missing_abstract_count,
    )

    # Phase 2: Tri-model review loop
    logger.info("Phase 2: Tri-model review loop (%d papers)", len(papers_to_review))
    prompt_hashes = get_prompt_hashes(args.prompt_version)
    prompt_hash = prompt_hashes["combined"]

    results = []
    reviewer_failures_count = 0

    for i, paper in enumerate(papers_to_review, 1):
        # Get gate result if gating was enabled
        gate_result = paper_gate_results.get(paper["id"])
        gate_info = gate_result.to_dict() if gate_result else None

        if gate_info:
            bucket_str = f" [{gate_info['gate_bucket']}]"
            if gate_info.get("gate_audit_selected"):
                bucket_str += " (audit)"
        else:
            bucket_str = ""

        logger.info("Reviewing paper %d/%d%s: %s", i, len(papers_to_review), bucket_str, paper["title"][:60])

        result = review_paper_with_tri_model(paper, available_reviewers)

        if result is None:
            reviewer_failures_count += 1
            continue

        # Add gate info to result
        if gate_info:
            result["gate_info"] = gate_info

        results.append(result)

        # Store to database
        eval_data = result["gpt_evaluation"]["evaluation"]

        # Extract latencies
        claude_latency = result["claude_review"].get("latency_ms") if result["claude_review"] and result["claude_review"].get("success") else None
        gemini_latency = result["gemini_review"].get("latency_ms") if result["gemini_review"] and result["gemini_review"].get("success") else None
        gpt_latency = result["gpt_evaluation"].get("latency_ms")

        # Extract prompt versions and model names
        prompt_versions = {
            "claude": args.prompt_version,
            "gemini": args.prompt_version,
            "gpt": args.prompt_version,
            "rubric_version": RELEVANCY_RUBRIC_VERSION,
            "prompt_hash": prompt_hash,
            "prompt_hashes": prompt_hashes,
        }

        model_names = {
            "claude": result["claude_review"].get("model") if result["claude_review"] and result["claude_review"].get("success") else None,
            "gemini": result["gemini_review"].get("model") if result["gemini_review"] and result["gemini_review"].get("success") else None,
            "gpt": result["gpt_evaluation"].get("model"),
        }

        # Extract credibility data
        cred_data = result.get("credibility", {})

        # Extract individual reviewer scores for centralized storage
        _claude_score = None
        _gemini_score = None
        if result["claude_review"] and result["claude_review"].get("success"):
            _claude_score = result["claude_review"].get("review", {}).get("relevancy_score")
        if result["gemini_review"] and result["gemini_review"].get("success"):
            _gemini_score = result["gemini_review"].get("review", {}).get("relevancy_score")

        if database_url:
            store.store_tri_model_scoring_event(
                run_id=run_id,
                mode="tri-model-daily",
                publication_id=paper["id"],
                title=paper["title"],
                source=paper["source"],
                published_date=paper.get("date"),
                claude_review=result["claude_review"].get("review") if result["claude_review"] and result["claude_review"].get("success") else None,
                gemini_review=result["gemini_review"].get("review") if result["gemini_review"] and result["gemini_review"].get("success") else None,
                gpt_eval=eval_data,
                final_relevancy_score=eval_data["final_relevancy_score"],
                final_relevancy_reason=eval_data["final_relevancy_reason"],
                final_signals=eval_data["final_signals"],
                final_summary=eval_data["final_summary"],
                agreement_level=eval_data["agreement_level"],
                disagreements=eval_data["disagreements"],
                evaluator_rationale=eval_data["evaluator_rationale"],
                confidence=eval_data["confidence"],
                prompt_versions=prompt_versions,
                model_names=model_names,
                claude_latency_ms=claude_latency,
                gemini_latency_ms=gemini_latency,
                gpt_latency_ms=gpt_latency,
                credibility_score=cred_data.get("credibility_score"),
                credibility_reason=cred_data.get("credibility_reason"),
                credibility_confidence=cred_data.get("credibility_confidence"),
                credibility_signals=cred_data.get("credibility_signals"),
                url=paper.get("url"),
                database_url=database_url,
            )
            # Dual-write: also update the publications row directly
            store.update_publication_scoring(
                publication_id=paper["id"],
                final_relevancy_score=eval_data["final_relevancy_score"],
                final_relevancy_reason=eval_data["final_relevancy_reason"],
                final_summary=eval_data["final_summary"],
                agreement_level=eval_data["agreement_level"],
                confidence=eval_data["confidence"],
                credibility_score=cred_data.get("credibility_score"),
                credibility_reason=cred_data.get("credibility_reason"),
                credibility_confidence=cred_data.get("credibility_confidence"),
                credibility_signals=cred_data.get("credibility_signals"),
                claude_score=_claude_score,
                gemini_score=_gemini_score,
                evaluator_rationale=eval_data["evaluator_rationale"],
                disagreements=eval_data["disagreements"],
                final_signals=eval_data["final_signals"],
                scoring_run_id=run_id,
                database_url=database_url,
            )
        else:
            store.store_tri_model_scoring_event(
                run_id=run_id,
                mode="tri-model-daily",
                publication_id=paper["id"],
                title=paper["title"],
                source=paper["source"],
                published_date=paper.get("date"),
                claude_review=result["claude_review"].get("review") if result["claude_review"] and result["claude_review"].get("success") else None,
                gemini_review=result["gemini_review"].get("review") if result["gemini_review"] and result["gemini_review"].get("success") else None,
                gpt_eval=eval_data,
                final_relevancy_score=eval_data["final_relevancy_score"],
                final_relevancy_reason=eval_data["final_relevancy_reason"],
                final_signals=eval_data["final_signals"],
                final_summary=eval_data["final_summary"],
                agreement_level=eval_data["agreement_level"],
                disagreements=eval_data["disagreements"],
                evaluator_rationale=eval_data["evaluator_rationale"],
                confidence=eval_data["confidence"],
                prompt_versions=prompt_versions,
                model_names=model_names,
                claude_latency_ms=claude_latency,
                gemini_latency_ms=gemini_latency,
                gpt_latency_ms=gpt_latency,
                credibility_score=cred_data.get("credibility_score"),
                credibility_reason=cred_data.get("credibility_reason"),
                credibility_confidence=cred_data.get("credibility_confidence"),
                credibility_signals=cred_data.get("credibility_signals"),
                url=paper.get("url"),
                db_path=db_path,
            )
            # Dual-write: also update the publications row directly
            store.update_publication_scoring(
                publication_id=paper["id"],
                final_relevancy_score=eval_data["final_relevancy_score"],
                final_relevancy_reason=eval_data["final_relevancy_reason"],
                final_summary=eval_data["final_summary"],
                agreement_level=eval_data["agreement_level"],
                confidence=eval_data["confidence"],
                credibility_score=cred_data.get("credibility_score"),
                credibility_reason=cred_data.get("credibility_reason"),
                credibility_confidence=cred_data.get("credibility_confidence"),
                credibility_signals=cred_data.get("credibility_signals"),
                claude_score=_claude_score,
                gemini_score=_gemini_score,
                evaluator_rationale=eval_data["evaluator_rationale"],
                disagreements=eval_data["disagreements"],
                final_signals=eval_data["final_signals"],
                scoring_run_id=run_id,
                db_path=db_path,
            )

    logger.info(
        "Tri-model review complete: %d successful, %d failures",
        len(results),
        reviewer_failures_count,
    )

    # Phase 3: Export tri-model events to JSONL
    logger.info("Phase 3: Exporting tri-model events to JSONL")
    events_path = run_output_dir / "tri_model_events.jsonl"
    if database_url:
        export_result = store.export_tri_model_events_to_jsonl(run_id, str(events_path), database_url)
    else:
        export_result = store.export_tri_model_events_to_jsonl(run_id, str(events_path), db_path)

    if export_result["success"]:
        logger.info("Exported %d events to %s", export_result["events_exported"], events_path)
    else:
        logger.warning("Failed to export events: %s", export_result["error"])

    # Phase 4: Generate must-reads (top 5)
    logger.info("Phase 4: Generating must-reads")

    if len(results) == 0:
        logger.warning("No successful tri-model reviews, writing empty outputs")

        # Write empty must-reads
        must_reads_data = {
            "run_id": run_id,
            "generated_at": datetime.now().isoformat(),
            "total_candidates": len(papers_to_review),
            "must_reads_count": 0,
            "reason": "No successful tri-model reviews",
            "must_reads": [],
        }

        must_reads_path = run_output_dir / "must_reads.json"
        with open(must_reads_path, "w", encoding="utf-8") as f:
            json.dump(must_reads_data, f, indent=2, ensure_ascii=False)

        # Write minimal report
        report_path = run_output_dir / "report.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"# Tri-Model Daily Run: {run_id}\n\n")
            f.write(f"**Status:** No successful tri-model reviews\n\n")
            f.write(f"**Total Candidates:** {len(papers_to_review)}\n\n")
            f.write(f"**Reviewer Failures:** {reviewer_failures_count}\n\n")
    else:
        must_reads_data = write_must_reads(run_id, results, run_output_dir, top_n=5)
        write_report(run_id, must_reads_data, run_output_dir, window_start, window_end)

    # Phase 5: Write manifest
    logger.info("Phase 5: Writing manifest")
    write_manifest(
        run_id=run_id,
        output_dir=run_output_dir,
        window_start=window_start,
        window_end=window_end,
        raw_fetched_count=raw_fetched_count,
        window_filtered_count=window_filtered_count,
        deduplicated_count=dedupe_stats["total_output"],
        usable_count=len(all_papers),  # Total usable before gating
        missing_abstract_count=missing_abstract_count,
        reviewer_failures_count=reviewer_failures_count,
        gpt_eval_count=len(results),
        available_reviewers=available_reviewers,
        window_mode=window_mode,
        matched_daily_run_id=matched_daily_run_id,
        prompt_version=args.prompt_version,
        rubric_version=RELEVANCY_RUBRIC_VERSION,
        prompt_hash=prompt_hash,
        experiment_id=args.experiment_id,
        gating_enabled=args.gating_enabled,
        gating_stats=gating_stats,
        gating_config_hashes=gating_config_hashes,
        gate_audit_rate=args.gate_audit_rate,
    )

    # Phase 6: Upload to Drive (optional)
    if args.upload_drive:
        logger.info("Phase 6: Uploading to Google Drive")
        try:
            from integrations.drive_upload import upload_tri_model_daily_outputs

            drive_result = upload_tri_model_daily_outputs(
                run_id=run_id,
                output_dir=run_output_dir,
                events_path=str(events_path) if events_path.exists() else None,
            )

            if drive_result.get("success"):
                logger.info("Drive upload successful: %s", drive_result.get("folder_url"))
            else:
                logger.warning("Drive upload failed: %s", drive_result.get("error"))
        except ImportError:
            logger.warning("Drive upload module not available, skipping")

    # Phase 7: Ingest to backend (optional)
    ingestion_failed = False
    if args.ingest_backend:
        logger.info("Phase 7: Ingesting outputs to backend")

        # Get backend credentials
        backend_url = args.backend_url or os.getenv("BACKEND_URL")
        backend_api_key = args.backend_api_key or os.getenv("BACKEND_API_KEY")

        if not backend_url or not backend_api_key:
            logger.warning("Backend ingestion requested but credentials not provided")
            print("\n⚠️  WARNING: Backend ingestion skipped (missing BACKEND_URL or BACKEND_API_KEY)")
            if args.ingest_strict:
                print("   Exiting with error code due to --ingest-strict flag\n")
                sys.exit(1)
        else:
            try:
                # Import ingestion functions
                sys.path.insert(0, str(Path(__file__).parent / "scripts"))
                from ingest_to_backend import (
                    ingest_manifest,
                    ingest_must_reads,
                    ingest_tri_model_events,
                    load_json_file,
                    load_jsonl_file,
                )

                # Load data files
                manifest_data = load_json_file(run_output_dir / "manifest.json")
                must_reads_data = load_json_file(run_output_dir / "must_reads.json")
                events = load_jsonl_file(run_output_dir / "tri_model_events.jsonl")

                # Ingest manifest
                manifest_result = ingest_manifest(
                    backend_url=backend_url,
                    api_key=backend_api_key,
                    manifest_data=manifest_data,
                    timeout=60,
                    retries=3,
                    dry_run=False,
                )

                if not manifest_result["success"]:
                    logger.error("Backend manifest ingestion failed")
                    ingestion_failed = True
                else:
                    # Ingest must-reads
                    must_reads_result = ingest_must_reads(
                        backend_url=backend_url,
                        api_key=backend_api_key,
                        run_id=run_id,
                        mode="tri-model-daily",
                        must_reads_data=must_reads_data,
                        timeout=60,
                        retries=3,
                        dry_run=False,
                    )

                    if not must_reads_result["success"]:
                        logger.error("Backend must-reads ingestion failed")
                        ingestion_failed = True
                    else:
                        # Ingest tri-model events
                        events_result = ingest_tri_model_events(
                            backend_url=backend_url,
                            api_key=backend_api_key,
                            run_id=run_id,
                            mode="tri-model-daily",
                            events=events,
                            chunk_size=args.ingest_chunk_size,
                            timeout=60,
                            retries=3,
                            dry_run=False,
                        )

                        if not events_result["success"]:
                            logger.error("Backend tri-model events ingestion failed")
                            ingestion_failed = True
                        else:
                            logger.info("Backend ingestion successful")
                            print(f"\n✓ Backend ingestion complete ({len(events)} events)")

                if ingestion_failed:
                    print("\n⚠️  WARNING: Backend ingestion failed (see logs above)")
                    if args.ingest_strict:
                        print("   Exiting with error code due to --ingest-strict flag\n")
                        sys.exit(1)

            except Exception as e:
                logger.error(f"Backend ingestion exception: {e}")
                print(f"\n⚠️  WARNING: Backend ingestion failed with exception: {e}")
                if args.ingest_strict:
                    print("   Exiting with error code due to --ingest-strict flag\n")
                    sys.exit(1)
                ingestion_failed = True

    # Final summary
    print("\n" + "=" * 70)
    print("TRI-MODEL DAILY RUN COMPLETE")
    print("=" * 70)
    print(f"Run ID:          {run_id}")
    print(f"Window:          {window_start.strftime('%Y-%m-%d %H:%M:%S')} to {window_end.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Raw fetched:     {raw_fetched_count} candidates")
    print(f"Window filtered: {window_filtered_count} candidates")
    print(f"Deduplicated:    {dedupe_stats['total_output']} candidates")
    print(f"Usable:          {len(papers_to_review)} papers")
    print(f"Must-Reads:      {len(must_reads_data.get('must_reads', []))} papers")
    print(f"Output dir:      {run_output_dir}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
