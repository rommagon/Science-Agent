#!/usr/bin/env python3
"""Run tri-model scoring on a benchmark evaluation dataset.

This script runs the existing tri-model scoring pipeline on a predetermined
set of publications (benchmark set), stores results in the DB with an
experiment_id, and writes artifacts to disk.

This is designed for evaluating prompt changes and calibration. Results can
be analyzed using scripts/evaluate_relevancy.py with --experiment-id.

Usage:
    python scripts/run_tri_model_benchmark.py \
        --eval-dataset data/eval_dataset.json \
        --experiment-id prompt-v2-test-1 \
        --prompt-version v2

    # Run on specific publications by ID
    python scripts/run_tri_model_benchmark.py \
        --publication-ids abc123,def456,ghi789 \
        --experiment-id mrd-test

    # With custom output directory
    python scripts/run_tri_model_benchmark.py \
        --eval-dataset data/eval_dataset.json \
        --experiment-id prompt-v2-test-1 \
        --output-dir data/outputs/benchmarks/prompt-v2-test-1

Input formats (eval_dataset.json):
    Option 1 - URL/DOI/PMID seeds (like seeds.json):
    [
        {"type": "pmid", "value": "39385123"},
        {"type": "doi", "value": "10.1038/s41586-024-07051-0"},
        {"type": "url", "value": "https://www.nature.com/articles/..."}
    ]

    Option 2 - Full evaluation items with human labels:
    [
        {
            "publication_id": "abc123",
            "title": "Multi-cancer early detection...",
            "doi": "10.1038/...",
            "abstract": "Background: ...",
            "human_labels": [
                {"source": "udi", "rating_0_3": 3, "rationale": "..."}
            ]
        }
    ]

Output:
    - benchmark_results.json: Full results with experiment metadata
    - tri_model_events.jsonl: Standard JSONL format (compatible with backend)
    - manifest.json: Run metadata
    - Results stored in SQLite with experiment_id in run_id

Environment variables:
    - CLAUDE_API_KEY: Required for Claude reviewer
    - GEMINI_API_KEY: Required for Gemini reviewer
    - SPOTITEARLY_LLM_API_KEY: Required for GPT evaluator
"""

import argparse
import json
import logging
import os
import sys
import time
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Import from score_seed_papers.py for reuse
from scripts.score_seed_papers import (
    resolve_seed,
    generate_publication_id,
    build_paper_for_review,
    review_paper_with_tri_model,
    write_tri_model_events,
    write_manifest,
)
from storage.store import get_database_url, get_store
from tri_model.prompts import RUBRIC_VERSION, get_prompt_hashes

# Import from scoring_eval for dataset loading
from scoring_eval.datasets import (
    extract_doi_from_url,
    load_udi_ground_truth,
    normalize_doi,
    normalize_pmid,
    normalize_title,
)

# Version for benchmark mode
BENCHMARK_VERSION = "v1"


def get_available_reviewers_from_env() -> List[str]:
    reviewers = []
    if os.getenv("CLAUDE_API_KEY"):
        reviewers.append("claude")
    if os.getenv("GEMINI_API_KEY"):
        reviewers.append("gemini")
    return reviewers


def validate_benchmark_config() -> Dict[str, Any]:
    """Validate benchmark configuration regardless of TRI_MODEL_MINI_DAILY."""
    reviewers = get_available_reviewers_from_env()
    errors = []

    if not reviewers:
        errors.append("No reviewer API keys configured (need CLAUDE_API_KEY or GEMINI_API_KEY)")

    openai_key = os.getenv("SPOTITEARLY_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not openai_key:
        errors.append("No OpenAI API key for GPT evaluator (need SPOTITEARLY_LLM_API_KEY or OPENAI_API_KEY)")

    return {
        "valid": not errors,
        "errors": errors,
        "details": f"{len(errors)} configuration error(s) found" if errors else None,
    }


def load_eval_dataset(file_path: Path) -> List[Dict[str, Any]]:
    """Load evaluation dataset from JSON file.

    Supports two formats:
    1. Seeds format: [{"type": "pmid", "value": "..."}, ...]
    2. Full items format: [{"publication_id": "...", "title": "...", ...}, ...]

    Args:
        file_path: Path to JSON file

    Returns:
        List of items in normalized format for processing
    """
    if not file_path.exists():
        raise ValueError(f"File not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        # Handle wrapped format
        if isinstance(data, dict):
            for key in ("data", "items", "records", "publications"):
                if key in data and isinstance(data[key], list):
                    data = data[key]
                    break
            else:
                raise ValueError(f"Expected list or dict with data/items key, got {type(data)}")
        else:
            raise ValueError(f"Expected list, got {type(data)}")

    if not data:
        raise ValueError("Dataset is empty")

    # Detect format based on first item
    first_item = data[0]
    if isinstance(first_item, dict) and "type" in first_item and "value" in first_item:
        logger.info("Detected seeds format (%d items)", len(data))
        return data
    elif isinstance(first_item, dict):
        logger.info("Detected full items format (%d items)", len(data))
        # Convert to seeds-like format for processing
        return _convert_items_to_seeds(data)
    else:
        raise ValueError(f"Unknown item format: {type(first_item)}")


def _convert_items_to_seeds(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert full items format to seeds format for processing.

    Args:
        items: Full evaluation items

    Returns:
        Seeds-format list with type/value pairs and preserved metadata
    """
    seeds = []

    for item in items:
        # Prefer DOI, then PMID, then URL
        doi = normalize_doi(item.get("doi"))
        pmid = normalize_pmid(item.get("pmid"))
        url = item.get("url")
        title = item.get("title", "")

        if not url and isinstance(title, str) and title.strip().lower().startswith(("http://", "https://")):
            # Defensive fix for datasets where URL accidentally lands in title field.
            url = title.strip()
            doi = doi or extract_doi_from_url(url)

        seed: Dict[str, Any] = {}

        if doi:
            seed["type"] = "doi"
            seed["value"] = doi
        elif pmid:
            seed["type"] = "pmid"
            seed["value"] = pmid
        elif url:
            seed["type"] = "url"
            seed["value"] = url
        elif item.get("publication_id"):
            # Items with a publication_id can be looked up from the database
            seed["type"] = "publication_id"
            seed["value"] = item["publication_id"]
        else:
            # Use title as identifier (will need to be resolved)
            seed["type"] = "title"
            seed["value"] = item.get("title", "")

        # Preserve metadata for later use
        seed["_original_item"] = item

        seeds.append(seed)

    return seeds


def load_publication_ids(
    publication_ids: str,
    db_path: str = "data/db/acitrack.db",
) -> List[Dict[str, Any]]:
    """Load publications by ID from database.

    Args:
        publication_ids: Comma-separated list of publication IDs
        db_path: Path to SQLite database

    Returns:
        List of items in seeds format
    """
    ids = [id.strip() for id in publication_ids.split(",") if id.strip()]
    if not ids:
        raise ValueError("No publication IDs provided")

    logger.info("Loading %d publications from database", len(ids))

    items = []
    for pub_id in ids:
        publication = _get_publication_from_store(pub_id, db_path)
        if publication:
            original_item = _publication_to_original_item(publication)
            item = {
                "type": "publication_id",
                "value": publication.get("id") or pub_id,
                "_original_item": original_item,
            }
            items.append(item)
        else:
            logger.warning("Publication not found: %s", pub_id)

    if not items:
        raise ValueError("No publications found for provided IDs")

    logger.info("Loaded %d publications from database", len(items))
    return items


def _get_publication_from_store(publication_id: str, db_path: str) -> Optional[Dict[str, Any]]:
    store = get_store()
    database_url = get_database_url()

    if database_url:
        logger.debug("Using PostgreSQL store for publication lookup")
        return store.get_publication_by_id(publication_id, database_url=database_url)

    logger.debug("Using SQLite store for publication lookup")
    return store.get_publication_by_id(publication_id, db_path=db_path)


def _publication_to_original_item(publication: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "publication_id": publication.get("id") or publication.get("publication_id"),
        "title": publication.get("title"),
        "source": publication.get("source"),
        "url": publication.get("canonical_url") or publication.get("url"),
        "abstract": publication.get("raw_text") or publication.get("summary"),
        "doi": publication.get("doi"),
        "pmid": publication.get("pmid"),
        "published_date": publication.get("published_date"),
    }


def build_paper_from_item(
    item: Dict[str, Any],
    publication_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build paper dict from original item (bypassing resolution).

    Args:
        item: Original item with title/abstract/etc.
        publication_id: Optional override for publication ID

    Returns:
        Paper dictionary for tri-model review
    """
    original = item.get("_original_item", {})

    title = original.get("title") or ""
    fallback_id = hashlib.sha256(title.encode("utf-8")).hexdigest()[:8] if title else "unknown"

    return {
        "id": publication_id or original.get("publication_id") or f"eval-{fallback_id}",
        "title": title or "Unknown Title",
        "source": original.get("source") or "evaluation",
        "date": original.get("published_date"),
        "url": original.get("url"),
        "raw_text": original.get("abstract") or original.get("raw_text") or "",
        "doi": original.get("doi"),
        "pmid": original.get("pmid"),
    }


def run_benchmark(
    items: List[Dict[str, Any]],
    experiment_id: str,
    prompt_version: str,
    output_dir: Path,
    available_reviewers: List[str],
    db_path: str = "data/db/acitrack.db",
    skip_resolution: bool = False,
) -> Dict[str, Any]:
    """Run benchmark scoring on items.

    Args:
        items: Items to score (seeds or full items format)
        experiment_id: Unique experiment identifier
        prompt_version: Version of prompts to use (v1, v2, or v3)
        output_dir: Output directory for artifacts
        available_reviewers: List of available reviewers
        db_path: Path to SQLite database
        skip_resolution: Skip DOI/PMID resolution (use original metadata)

    Returns:
        Benchmark results summary
    """
    run_id = f"benchmark-{experiment_id}"
    mode = "tri-model-benchmark"
    total_items = len(items)
    prompt_hashes = get_prompt_hashes(prompt_version)
    prompt_hash = prompt_hashes["combined"]

    logger.info("Starting benchmark run: %s", run_id)
    logger.info("  Experiment ID: %s", experiment_id)
    logger.info("  Prompt version: %s", prompt_version)
    logger.info("  Total items: %d", total_items)
    logger.info("  Output dir: %s", output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1: Resolve or build papers
    resolved_papers = []
    resolution_failures = []

    for i, item in enumerate(items, 1):
        try:
            item_type = item.get("type", "")
            item_value = item.get("value", "")
            if item_type == "title" and str(item_value).strip().lower().startswith(("http://", "https://")):
                item_type = "url"
                item["type"] = "url"

            logger.info("Processing item %d/%d: %s=%s", i, total_items, item_type, str(item_value)[:50])

            # Check if we can skip resolution
            original_item = item.get("_original_item")
            if skip_resolution and original_item and original_item.get("title") and original_item.get("abstract"):
                # Build paper directly from original metadata
                publication_id = original_item.get("publication_id") or generate_publication_id(item, original_item)
                paper = build_paper_from_item(item, publication_id)
                resolved_papers.append({
                    "paper": paper,
                    "original_item": original_item,
                    "resolved": original_item,  # Use original as "resolved"
                })
                continue

            # Handle publication_id type (already in DB)
            if item_type == "publication_id":
                if not original_item:
                    publication = _get_publication_from_store(str(item_value), db_path)
                    if publication:
                        original_item = _publication_to_original_item(publication)
                        item["_original_item"] = original_item
                paper = build_paper_from_item(item, publication_id=str(item_value))
                resolved_papers.append({
                    "paper": paper,
                    "original_item": original_item,
                    "resolved": original_item or {},
                })
                continue

            # Standard resolution for DOI/PMID/URL
            resolved = resolve_seed(item)

            if resolved.get("resolution_error") and not resolved.get("title"):
                logger.warning("Failed to resolve item: %s", resolved.get("resolution_error"))
                resolution_failures.append({
                    "item": item,
                    "error": resolved.get("resolution_error"),
                })
                continue

            publication_id = generate_publication_id(item, resolved)
            paper = build_paper_for_review(resolved, publication_id)

            # Preserve human labels from original item
            if original_item:
                paper["_human_labels"] = original_item.get("human_labels", [])

            resolved_papers.append({
                "paper": paper,
                "original_item": original_item,
                "resolved": resolved,
            })

            # Rate limiting for APIs
            time.sleep(0.3)
        except Exception:
            logger.exception("Unexpected error processing item %d/%d", i, total_items)
            resolution_failures.append({
                "item": item,
                "error": "Unexpected error during resolution",
            })

    resolved_count = len(resolved_papers)
    failed_resolution_count = len(resolution_failures)

    logger.info("Resolution complete: %d resolved, %d failed", resolved_count, failed_resolution_count)

    if resolved_count == 0:
        raise RuntimeError("No items could be resolved. Check input data.")

    # Phase 2: Run tri-model scoring
    logger.info("Phase 2: Running tri-model scoring on %d papers", resolved_count)
    results = []
    reviewer_failures_count = 0

    for i, item_data in enumerate(resolved_papers, 1):
        try:
            paper = item_data["paper"]
            original_item = item_data.get("original_item")

            logger.info("Scoring paper %d/%d: %s", i, resolved_count, str(paper["title"])[:60])

            # Skip if no content
            if paper["title"] == "Unknown Title" and not paper.get("raw_text"):
                missing_fields = []
                if not paper.get("title") or paper.get("title") == "Unknown Title":
                    missing_fields.append("title")
                if not paper.get("raw_text"):
                    missing_fields.append("abstract")
                logger.warning(
                    "Skipping paper with missing fields (%s) for publication_id=%s",
                    ", ".join(missing_fields),
                    paper.get("id"),
                )
                reviewer_failures_count += 1
                continue

            result = review_paper_with_tri_model(paper, available_reviewers)

            if result is None:
                reviewer_failures_count += 1
                continue

            # Add metadata
            result["url"] = paper.get("url")
            result["experiment_id"] = experiment_id
            result["prompt_version"] = prompt_version

            # Preserve human labels for later evaluation
            if original_item and original_item.get("human_labels"):
                result["human_labels"] = original_item["human_labels"]

            results.append(result)
        except Exception:
            reviewer_failures_count += 1
            logger.exception("Unexpected error scoring paper %d/%d", i, resolved_count)

    scored_count = len(results)
    logger.info("Scoring complete: %d scored, %d failures", scored_count, reviewer_failures_count)

    # Phase 3: Write artifacts
    logger.info("Phase 3: Writing output artifacts")

    # Write tri_model_events.jsonl
    events_path = output_dir / "tri_model_events.jsonl"
    write_tri_model_events(run_id, mode, results, events_path)

    # Write manifest.json
    manifest_data = write_manifest(
        run_id=run_id,
        mode=mode,
        output_dir=output_dir,
        total_seeds=total_items,
        resolved_count=resolved_count,
        failed_resolution_count=failed_resolution_count,
        scored_count=scored_count,
        reviewer_failures_count=reviewer_failures_count,
        available_reviewers=available_reviewers,
    )

    # Add benchmark-specific fields to manifest
    manifest_data["experiment_id"] = experiment_id
    manifest_data["prompt_version"] = prompt_version
    manifest_data["rubric_version"] = RUBRIC_VERSION
    manifest_data["prompt_hash"] = prompt_hash
    manifest_data["prompt_hashes"] = prompt_hashes
    manifest_data["benchmark_version"] = BENCHMARK_VERSION

    # Re-write manifest with additional fields
    with open(output_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)

    # Write benchmark_results.json
    benchmark_results = {
        "experiment_id": experiment_id,
        "run_id": run_id,
        "prompt_version": prompt_version,
        "rubric_version": RUBRIC_VERSION,
        "prompt_hash": prompt_hash,
        "prompt_hashes": prompt_hashes,
        "benchmark_version": BENCHMARK_VERSION,
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_items": total_items,
            "resolved": resolved_count,
            "failed_resolution": failed_resolution_count,
            "scored": scored_count,
            "reviewer_failures": reviewer_failures_count,
        },
        "reviewers": available_reviewers,
        "results": results,
        "resolution_failures": resolution_failures,
    }

    results_path = output_dir / "benchmark_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(benchmark_results, f, indent=2, ensure_ascii=False)

    logger.info("Wrote benchmark results to %s", results_path)

    # Store to database (reuse existing storage functions)
    try:
        from storage.sqlite_store import store_tri_model_scoring_event

        for result in results:
            eval_data = result.get("gpt_evaluation", {}).get("evaluation", {})
            cred_data = result.get("credibility", {})

            claude_review = None
            if result.get("claude_review") and result["claude_review"].get("success"):
                claude_review = result["claude_review"].get("review")

            gemini_review = None
            if result.get("gemini_review") and result["gemini_review"].get("success"):
                gemini_review = result["gemini_review"].get("review")

            store_tri_model_scoring_event(
                run_id=run_id,
                mode=mode,
                publication_id=result.get("publication_id"),
                title=result.get("title"),
                source=result.get("source"),
                published_date=result.get("published_date"),
                claude_review=claude_review,
                gemini_review=gemini_review,
                gpt_eval=eval_data,
                final_relevancy_score=eval_data.get("final_relevancy_score"),
                final_relevancy_reason=eval_data.get("final_relevancy_reason"),
                final_signals=eval_data.get("final_signals"),
                final_summary=eval_data.get("final_summary"),
                agreement_level=eval_data.get("agreement_level"),
                disagreements=eval_data.get("disagreements"),
                evaluator_rationale=eval_data.get("evaluator_rationale"),
                confidence=eval_data.get("confidence"),
                prompt_versions={
                    "claude": prompt_version,
                    "gemini": prompt_version,
                    "gpt": prompt_version,
                    "rubric_version": RUBRIC_VERSION,
                    "prompt_hash": prompt_hash,
                    "prompt_hashes": prompt_hashes,
                },
                model_names={
                    "claude": result.get("claude_review", {}).get("model"),
                    "gemini": result.get("gemini_review", {}).get("model"),
                    "gpt": result.get("gpt_evaluation", {}).get("model"),
                },
                claude_latency_ms=result.get("claude_review", {}).get("latency_ms"),
                gemini_latency_ms=result.get("gemini_review", {}).get("latency_ms"),
                gpt_latency_ms=result.get("gpt_evaluation", {}).get("latency_ms"),
                credibility_score=cred_data.get("credibility_score"),
                credibility_reason=cred_data.get("credibility_reason"),
                credibility_confidence=cred_data.get("credibility_confidence"),
                credibility_signals=cred_data.get("credibility_signals"),
                db_path=db_path,
            )

        logger.info("Stored %d events to database", scored_count)

    except Exception as e:
        logger.warning("Failed to store to database: %s", e)

    return benchmark_results


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run tri-model scoring on benchmark evaluation dataset",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Input options (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--eval-dataset",
        type=Path,
        help="Path to evaluation dataset JSON file",
    )
    input_group.add_argument(
        "--publication-ids",
        type=str,
        help="Comma-separated list of publication IDs to score",
    )

    # Experiment tracking
    parser.add_argument(
        "--experiment-id",
        type=str,
        required=True,
        help="Unique experiment identifier (required)",
    )
    parser.add_argument(
        "--prompt-version",
        type=str,
        default="v2",
        choices=["v1", "v2", "v3"],
        help="Prompt version to use (default: v2)",
    )

    # Output options
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory (default: data/outputs/benchmarks/<experiment-id>/)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="data/db/acitrack.db",
        help="Path to SQLite database",
    )

    # Processing options
    parser.add_argument(
        "--max-items",
        type=int,
        help="Maximum number of items to process",
    )
    parser.add_argument(
        "--skip-resolution",
        action="store_true",
        help="Skip DOI/PMID resolution (use provided metadata)",
    )

    args = parser.parse_args()
    os.environ["TRI_MODEL_PROMPT_VERSION"] = args.prompt_version

    # Validate tri-model configuration
    validation_result = validate_benchmark_config()

    if not validation_result["valid"]:
        print("\n ERROR: Configuration validation failed:")
        for error in validation_result["errors"]:
            safe_error = error
            for env_var in ["CLAUDE_API_KEY", "GEMINI_API_KEY", "SPOTITEARLY_LLM_API_KEY"]:
                key = os.getenv(env_var)
                if key:
                    safe_error = safe_error.replace(key, "***")
            print(f"   - {safe_error}")
        print()
        return 1

    available_reviewers = get_available_reviewers_from_env()
    logger.info("Available reviewers: %s", available_reviewers)

    # Load items
    try:
        if args.eval_dataset:
            items = load_eval_dataset(args.eval_dataset)
        else:
            items = load_publication_ids(args.publication_ids, args.db_path)
    except Exception as e:
        logger.error("Failed to load input: %s", e)
        print(f"\n ERROR: {e}\n")
        return 1

    # Apply max-items limit
    if args.max_items and len(items) > args.max_items:
        logger.info("Limiting to %d items (from %d)", args.max_items, len(items))
        items = items[:args.max_items]

    # Setup output directory
    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = Path("data/outputs/benchmarks") / args.experiment_id

    output_dir.mkdir(parents=True, exist_ok=True)

    # Print execution plan
    print("\n" + "=" * 70)
    print("Tri-Model Benchmark Run")
    print("=" * 70)
    print(f"Experiment ID:   {args.experiment_id}")
    print(f"Prompt Version:  {args.prompt_version}")
    print(f"Total Items:     {len(items)}")
    print(f"Reviewers:       {', '.join(available_reviewers)}")
    print(f"Output Dir:      {output_dir}")
    database_url = get_database_url()
    if database_url:
        print("Database:        PostgreSQL (DATABASE_URL configured)")
    else:
        print(f"Database:        {args.db_path}")
    print("=" * 70 + "\n")

    # Run benchmark
    try:
        results = run_benchmark(
            items=items,
            experiment_id=args.experiment_id,
            prompt_version=args.prompt_version,
            output_dir=output_dir,
            available_reviewers=available_reviewers,
            db_path=args.db_path,
            skip_resolution=args.skip_resolution,
        )
    except Exception as e:
        logger.exception("Benchmark failed")
        print(f"\n ERROR: Benchmark failed: {e}\n")
        return 1

    # Print summary
    summary = results.get("summary", {})
    print("\n" + "=" * 70)
    print("BENCHMARK COMPLETE")
    print("=" * 70)
    print(f"Experiment ID:    {args.experiment_id}")
    print(f"Run ID:           {results.get('run_id')}")
    print(f"Total Items:      {summary.get('total_items')}")
    print(f"Resolved:         {summary.get('resolved')}")
    print(f"Failed Resolve:   {summary.get('failed_resolution')}")
    print(f"Scored:           {summary.get('scored')}")
    print(f"Reviewer Failures:{summary.get('reviewer_failures')}")
    print(f"\nOutputs:")
    print(f"  benchmark_results.json: {output_dir / 'benchmark_results.json'}")
    print(f"  tri_model_events.jsonl: {output_dir / 'tri_model_events.jsonl'}")
    print(f"  manifest.json:          {output_dir / 'manifest.json'}")
    print(f"\nNext Steps:")
    print(f"  Evaluate with: python scripts/evaluate_relevancy.py \\")
    print(f"      --udi-file <eval-dataset> --experiment-id {args.experiment_id}")
    print("=" * 70 + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
