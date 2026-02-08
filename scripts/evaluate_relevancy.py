#!/usr/bin/env python3
"""Evaluation harness for relevancy scoring.

This script evaluates the tri-model relevancy scoring pipeline against
human ground truth datasets (Udi's rankings and calibration surveys).

Usage:
    python scripts/evaluate_relevancy.py \
        --udi-file data/ground_truth/udi_rankings.json \
        --survey-file data/ground_truth/calibration_survey.csv \
        --run-scorer \
        --output-dir data/outputs/scoring-eval/2026-01-24/

Outputs:
    - eval_report.md: Human-readable report with metrics and disagreements
    - eval_results.json: Full per-item results
    - eval_results.csv: Optional CSV export
    - calibration_params.json: Calibration mapping (if --calibrate)
"""

import argparse
import csv
import json
import logging
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scoring_eval.datasets import (
    extract_doi_from_url,
    enrich_items_with_tri_model,
    load_calibration_survey,
    load_tri_model_results_from_db,
    load_udi_ground_truth,
    merge_datasets,
    normalize_title,
)
from scoring_eval.metrics import (
    compute_all_metrics,
    compute_metrics_by_source,
    find_top_disagreements,
)
from scoring_eval.calibration import (
    IsotonicCalibrator,
    apply_calibration_to_items,
    fit_calibrator_from_items,
    validate_calibrator_bounds,
    validate_calibrator_monotonicity,
)

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Evaluate relevancy scoring against human ground truth",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Input files
    parser.add_argument(
        "--udi-file",
        type=Path,
        help="Path to Udi ground truth file (JSON/CSV)",
    )
    parser.add_argument(
        "--survey-file",
        type=Path,
        help="Path to calibration survey file (JSON/CSV)",
    )

    # Options
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Maximum number of items to evaluate",
    )
    parser.add_argument(
        "--run-scorer",
        action="store_true",
        help="Run tri-model scoring on evaluation items (vs using existing DB scores)",
    )
    parser.add_argument(
        "--tri-model-run-id",
        type=str,
        default=None,
        help="Use scores from specific tri-model run_id (for baseline comparison)",
    )
    parser.add_argument(
        "--experiment-id",
        type=str,
        default=None,
        help="Experiment ID for tracking. When loading from DB (not --run-scorer), "
             "this filters to scores from benchmark run with this experiment_id",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="Fit and apply isotonic calibration",
    )

    # Output
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: data/outputs/scoring-eval/<timestamp>/)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="data/db/acitrack.db",
        help="Path to SQLite database",
    )
    parser.add_argument(
        "--export-csv",
        action="store_true",
        help="Export results to CSV in addition to JSON",
    )

    # Debug
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    return parser.parse_args()


def run_tri_model_scoring(
    items: List[Dict[str, Any]],
    experiment_id: str,
    db_path: str,
) -> List[Dict[str, Any]]:
    """Run tri-model scoring on evaluation items.

    This re-scores items using the current tri-model pipeline configuration.

    Args:
        items: Evaluation items with title/abstract
        experiment_id: Experiment identifier for run_id
        db_path: Path to database

    Returns:
        Items enriched with model scores
    """
    logger.info("Running tri-model scoring on %d items...", len(items))

    try:
        # Current API names
        from tri_model.reviewers import claude_review as _claude_review
        from tri_model.reviewers import gemini_review as _gemini_review
        from tri_model.evaluator import gpt_evaluate as _gpt_evaluate
        from config.tri_model_config import (
            CLAUDE_REVIEW_VERSION,
            GEMINI_REVIEW_VERSION,
            GPT_EVALUATOR_VERSION,
        )
        from storage.sqlite_store import store_tri_model_scoring_event
    except ImportError:
        # Backward-compatible fallback names
        try:
            from tri_model.reviewers import review_paper_claude as _claude_review
            from tri_model.reviewers import review_paper_gemini as _gemini_review
            from tri_model.evaluator import evaluate_reviews as _gpt_evaluate
            from config.tri_model_config import (
                CLAUDE_REVIEW_VERSION,
                GEMINI_REVIEW_VERSION,
                GPT_EVALUATOR_VERSION,
            )
            from storage.sqlite_store import store_tri_model_scoring_event
        except ImportError as e:
            logger.error("Failed to import tri-model modules: %s", e)
            raise

    results = []
    run_id = f"scoring-eval-{experiment_id}"

    for idx, item in enumerate(items):
        logger.info(
            "Scoring item %d/%d: %s",
            idx + 1,
            len(items),
            item.get("title", "")[:60],
        )

        # Build paper dict for tri-model
        paper = {
            "id": item.get("publication_id") or f"eval-{idx}",
            "title": item.get("title", ""),
            "source": item.get("source", "evaluation"),
            "date": datetime.now().isoformat(),
            "url": item.get("url", ""),
            "raw_text": item.get("abstract", ""),
        }

        # Run Claude review
        claude_result = _claude_review(paper)
        claude_review = claude_result.get("review") if claude_result.get("success") else None

        # Run Gemini review
        gemini_result = _gemini_review(paper)
        gemini_review = gemini_result.get("review") if gemini_result.get("success") else None

        # Run GPT evaluator
        eval_result = {
            "success": False,
            "evaluation": None,
            "model": "",
            "latency_ms": 0,
        }
        if claude_review or gemini_review:
            # New API expects full reviewer result dicts; legacy may expect review payloads.
            try:
                eval_result = _gpt_evaluate(paper, claude_result, gemini_result)
            except TypeError:
                eval_result = _gpt_evaluate(paper, claude_review, gemini_review)
            gpt_eval = eval_result.get("evaluation") if eval_result.get("success") else None
        else:
            gpt_eval = None
            logger.warning("No reviews available for GPT evaluation")

        # Extract final score
        final_score = None
        final_reason = None

        if gpt_eval:
            final_score = gpt_eval.get("final_relevancy_score")
            final_reason = gpt_eval.get("final_relevancy_reason")
        elif claude_review:
            final_score = claude_review.get("relevancy_score")
            final_reason = claude_review.get("relevancy_reason")
        elif gemini_review:
            final_score = gemini_review.get("relevancy_score")
            final_reason = gemini_review.get("relevancy_reason")

        # Store to database
        try:
            store_tri_model_scoring_event(
                run_id=run_id,
                mode="scoring-eval",
                publication_id=paper["id"],
                title=paper["title"],
                source=paper["source"],
                published_date=paper["date"],
                claude_review=claude_review,
                gemini_review=gemini_review,
                gpt_eval=gpt_eval,
                final_relevancy_score=final_score,
                final_relevancy_reason=final_reason,
                final_signals=gpt_eval.get("final_signals") if gpt_eval else None,
                final_summary=gpt_eval.get("final_summary") if gpt_eval else None,
                agreement_level=gpt_eval.get("agreement_level") if gpt_eval else None,
                disagreements=gpt_eval.get("disagreements") if gpt_eval else None,
                evaluator_rationale=gpt_eval.get("evaluator_rationale") if gpt_eval else None,
                confidence=gpt_eval.get("confidence") if gpt_eval else None,
                prompt_versions={
                    "claude": CLAUDE_REVIEW_VERSION,
                    "gemini": GEMINI_REVIEW_VERSION,
                    "gpt": GPT_EVALUATOR_VERSION,
                },
                model_names={
                    "claude": claude_result.get("model", ""),
                    "gemini": gemini_result.get("model", ""),
                    "gpt": eval_result.get("model", "") if gpt_eval else "",
                },
                claude_latency_ms=claude_result.get("latency_ms", 0),
                gemini_latency_ms=gemini_result.get("latency_ms", 0),
                gpt_latency_ms=eval_result.get("latency_ms", 0) if gpt_eval else 0,
                db_path=db_path,
            )
        except Exception as e:
            logger.warning("Failed to store scoring event: %s", e)

        # Enrich item with results
        enriched = item.copy()
        enriched["model_score"] = final_score
        enriched["model_reason"] = final_reason
        enriched["model_signals"] = gpt_eval.get("final_signals") if gpt_eval else None
        enriched["model_confidence"] = gpt_eval.get("confidence") if gpt_eval else None
        enriched["model_agreement_level"] = gpt_eval.get("agreement_level") if gpt_eval else None
        enriched["claude_review"] = claude_review
        enriched["gemini_review"] = gemini_review
        enriched["gpt_eval"] = gpt_eval
        enriched["tri_model_run_id"] = run_id
        enriched["source_type"] = "tri_model_live"

        results.append(enriched)

    logger.info("Completed scoring %d items", len(results))
    return results


def prepare_items_for_metrics(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Prepare items with computed fields for metrics.

    Args:
        items: Raw evaluation items

    Returns:
        Items with mean_human_rating and udi_rating computed
    """
    def coerce_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            try:
                return int(float(value))
            except ValueError:
                return None
        return None

    def coerce_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            try:
                return float(value)
            except ValueError:
                return None
        return None

    def mean(values: List[float]) -> Optional[float]:
        if not values:
            return None
        return sum(values) / len(values)

    def majority_bucket(ratings: List[int]) -> Optional[int]:
        if not ratings:
            return None
        counts: Dict[int, int] = {}
        for rating in ratings:
            counts[rating] = counts.get(rating, 0) + 1
        return sorted(counts.items(), key=lambda x: (-x[1], -x[0]))[0][0]

    prepared = []
    for item in items:
        prepared_item = item.copy()

        labels = prepared_item.get("labels") or prepared_item.get("human_labels") or []
        normalized_labels = []
        for label in labels:
            source = label.get("source")
            if source == "udi":
                source = "udi_ground_truth"
            elif source == "survey":
                source = "calibration_survey"
            normalized = dict(label)
            normalized["source"] = source
            normalized_labels.append(normalized)

        prepared_item["labels"] = normalized_labels

        udi_labels = [l for l in normalized_labels if l.get("source") == "udi_ground_truth"]
        survey_labels = [l for l in normalized_labels if l.get("source") == "calibration_survey"]

        survey_ratings = [
            l.get("rating_0_3") for l in survey_labels
            if l.get("rating_0_3") is not None
        ]
        survey_raw_scores = [
            coerce_float(l.get("rating_raw")) for l in survey_labels
            if coerce_float(l.get("rating_raw")) is not None
        ]

        prepared_item["mean_human_rating"] = mean([float(r) for r in survey_ratings])
        prepared_item["mean_human_rating_raw"] = mean(survey_raw_scores)
        prepared_item["majority_bucket"] = majority_bucket([coerce_int(r) for r in survey_ratings if coerce_int(r) is not None])

        udi_ratings = [
            l.get("rating_0_3") for l in udi_labels
            if l.get("rating_0_3") is not None
        ]
        prepared_item["udi_rating"] = majority_bucket([coerce_int(r) for r in udi_ratings if coerce_int(r) is not None])

        prepared_item["human_labels"] = normalized_labels
        prepared_item["survey_labels"] = survey_labels
        prepared_item["udi_labels"] = udi_labels

        prepared.append(prepared_item)

    return prepared


def _load_unified_dataset(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise ValueError(f"File not found: {path}")

    if path.suffix.lower() == ".json":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key in ("data", "records", "items"):
                if key in data and isinstance(data[key], list):
                    return data[key]
            return [data]
        if isinstance(data, list):
            normalized = []
            for item in data:
                if not isinstance(item, dict):
                    normalized.append(item)
                    continue
                title = (item.get("title") or "").strip()
                if title.lower().startswith(("http://", "https://")) and not item.get("url"):
                    item = dict(item)
                    item["url"] = title
                    if not item.get("doi"):
                        item["doi"] = extract_doi_from_url(title)
                normalized.append(item)
            return normalized
        raise ValueError(f"Unexpected JSON structure in {path}")

    if path.suffix.lower() == ".csv":
        rows: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        grouped: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            pub_id = (row.get("publication_id") or "").strip()
            title = (row.get("title") or "").strip()
            url = (row.get("url") or "").strip()
            canonical_url = (row.get("canonical_url") or "").strip()
            if not url and title.lower().startswith(("http://", "https://")):
                url = title
            key = pub_id or normalize_title(title)
            if not key:
                continue

            entry = grouped.get(key)
            if entry is None:
                entry = {
                    "publication_id": pub_id or None,
                    "title": title,
                    "url": url or None,
                    "canonical_url": canonical_url or None,
                    "doi": (row.get("doi") or extract_doi_from_url(url)) if url else row.get("doi"),
                    "labels": [],
                }
                grouped[key] = entry

            label = {
                "source": row.get("label_source"),
                "rater": row.get("label_rater"),
                "rating_0_3": row.get("label_rating_0_3"),
                "rating_raw": row.get("label_rating_raw"),
                "rationale": row.get("label_rationale"),
                "confidence": row.get("label_confidence"),
            }
            entry["labels"].append(label)

        return list(grouped.values())

    raise ValueError(f"Unsupported file format: {path.suffix}")


def generate_report(
    items: List[Dict[str, Any]],
    metrics: Dict[str, Any],
    metrics_by_source: Dict[str, Dict[str, Any]],
    disagreements: List[Dict[str, Any]],
    calibration_info: Optional[Dict[str, Any]] = None,
    experiment_id: str = "",
) -> str:
    """Generate markdown evaluation report.

    Args:
        items: Evaluation items with scores
        metrics: Overall metrics
        metrics_by_source: Metrics broken down by source
        disagreements: Top disagreements
        calibration_info: Calibration information (if applied)
        experiment_id: Experiment identifier

    Returns:
        Markdown report string
    """
    lines = []

    # Header
    lines.append("# Relevancy Scoring Evaluation Report")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().isoformat()}")
    lines.append(f"**Experiment ID:** {experiment_id}")
    lines.append(f"**Total Items:** {len(items)}")

    # Count items with scores
    items_with_scores = [i for i in items if i.get("model_score") is not None]
    items_with_human = [i for i in items if i.get("mean_human_rating") is not None]
    items_with_udi = [i for i in items if i.get("udi_rating") is not None]

    lines.append(f"**Items with Model Scores:** {len(items_with_scores)}")
    lines.append(f"**Items with Human Ratings:** {len(items_with_human)}")
    lines.append(f"**Items with Udi Ratings:** {len(items_with_udi)}")
    lines.append("")

    # Overall Metrics
    lines.append("## Overall Metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")

    spearman = metrics.get("spearman_rho")
    lines.append(f"| Spearman ρ | {spearman:.3f} | " if spearman else "| Spearman ρ | N/A |")

    for k in [5, 10]:
        ndcg = metrics.get(f"ndcg@{k}")
        lines.append(f"| NDCG@{k} | {ndcg:.3f} |" if ndcg else f"| NDCG@{k} | N/A |")

    for k in [5, 10, 20]:
        recall = metrics.get(f"recall@{k}")
        lines.append(f"| Recall@{k} (rating=3) | {recall:.3f} |" if recall else f"| Recall@{k} | N/A |")

    if metrics.get("recall@5_total_relevant"):
        lines.append(
            f"| Total Rating=3 Items | {metrics['recall@5_total_relevant']} |"
        )

    lines.append("")

    # Metrics by Source
    if metrics_by_source:
        lines.append("## Metrics by Source Type")
        lines.append("")

        for source, source_metrics in metrics_by_source.items():
            lines.append(f"### {source.title()}")
            lines.append(f"- Items: {source_metrics.get('n_items', 0)}")

            spearman = source_metrics.get("spearman_rho")
            if spearman:
                lines.append(f"- Spearman ρ: {spearman:.3f}")

            ndcg = source_metrics.get("ndcg@5")
            if ndcg:
                lines.append(f"- NDCG@5: {ndcg:.3f}")

            lines.append("")

    # Calibration Info
    if calibration_info:
        lines.append("## Calibration")
        lines.append("")
        lines.append("Isotonic regression calibration was applied.")
        lines.append("")

        pre_metrics = calibration_info.get("pre_calibration_metrics", {})
        post_metrics = calibration_info.get("post_calibration_metrics", {})

        if pre_metrics and post_metrics:
            lines.append("| Metric | Pre-Calibration | Post-Calibration |")
            lines.append("|--------|-----------------|------------------|")

            pre_spearman = pre_metrics.get("spearman_rho")
            post_spearman = post_metrics.get("spearman_rho")
            lines.append(
                f"| Spearman ρ | {pre_spearman:.3f if pre_spearman else 'N/A'} | "
                f"{post_spearman:.3f if post_spearman else 'N/A'} |"
            )

            for k in [5, 10]:
                pre_ndcg = pre_metrics.get(f"ndcg@{k}")
                post_ndcg = post_metrics.get(f"ndcg@{k}")
                lines.append(
                    f"| NDCG@{k} | {pre_ndcg:.3f if pre_ndcg else 'N/A'} | "
                    f"{post_ndcg:.3f if post_ndcg else 'N/A'} |"
                )

            lines.append("")

        # Mapping table
        mapping = calibration_info.get("mapping_table", [])
        if mapping:
            lines.append("### Score Mapping Table")
            lines.append("")
            lines.append("| Raw Score | Calibrated Score |")
            lines.append("|-----------|------------------|")
            for raw, calibrated in mapping:
                lines.append(f"| {raw} | {calibrated} |")
            lines.append("")

    # Top Disagreements
    lines.append("## Top 20 Disagreements")
    lines.append("")
    lines.append(
        "Items with largest difference between model score (scaled to 0-3) and mean human rating:"
    )
    lines.append("")

    for i, d in enumerate(disagreements, 1):
        lines.append(f"### {i}. {d['title'][:80]}...")
        lines.append("")
        lines.append(f"- **Mean Human Rating:** {d['mean_human_rating']:.2f}")
        lines.append(f"- **Model Score:** {d['model_score']} (scaled: {d['model_score_scaled_0_3']})")
        lines.append(f"- **Absolute Error:** {d['absolute_error']:.2f}")

        if d.get("udi_rating") is not None:
            lines.append(f"- **Udi Rating:** {d['udi_rating']}")

        if d.get("model_reason"):
            lines.append(f"- **Model Reason:** {d['model_reason'][:200]}...")

        if d.get("human_rationale"):
            lines.append(f"- **Human Rationale:** {d['human_rationale'][:200]}...")

        lines.append("")

    # Score Distribution
    lines.append("## Score Distribution")
    lines.append("")

    # Model score distribution
    model_scores = [i["model_score"] for i in items if i.get("model_score") is not None]
    if model_scores:
        lines.append("### Model Scores (0-100)")
        lines.append(f"- Min: {min(model_scores)}")
        lines.append(f"- Max: {max(model_scores)}")
        lines.append(f"- Mean: {sum(model_scores)/len(model_scores):.1f}")

        # Buckets
        buckets = {
            "0-19": len([s for s in model_scores if s < 20]),
            "20-39": len([s for s in model_scores if 20 <= s < 40]),
            "40-59": len([s for s in model_scores if 40 <= s < 60]),
            "60-79": len([s for s in model_scores if 60 <= s < 80]),
            "80-100": len([s for s in model_scores if s >= 80]),
        }
        lines.append("")
        lines.append("| Range | Count |")
        lines.append("|-------|-------|")
        for range_str, count in buckets.items():
            lines.append(f"| {range_str} | {count} |")

    lines.append("")

    # Human rating distribution
    human_ratings = [i["mean_human_rating"] for i in items if i.get("mean_human_rating") is not None]
    if human_ratings:
        lines.append("### Human Ratings (0-3)")
        lines.append(f"- Min: {min(human_ratings):.2f}")
        lines.append(f"- Max: {max(human_ratings):.2f}")
        lines.append(f"- Mean: {sum(human_ratings)/len(human_ratings):.2f}")

        # Buckets
        buckets = {
            "0-0.9": len([r for r in human_ratings if r < 1]),
            "1-1.9": len([r for r in human_ratings if 1 <= r < 2]),
            "2-2.9": len([r for r in human_ratings if 2 <= r < 3]),
            "3": len([r for r in human_ratings if r == 3]),
        }
        lines.append("")
        lines.append("| Range | Count |")
        lines.append("|-------|-------|")
        for range_str, count in buckets.items():
            lines.append(f"| {range_str} | {count} |")

    lines.append("")

    return "\n".join(lines)


def export_results_json(
    items: List[Dict[str, Any]],
    metrics: Dict[str, Any],
    output_path: Path,
    experiment_id: str,
) -> None:
    """Export full results to JSON.

    Args:
        items: Evaluation items
        metrics: Computed metrics
        output_path: Output file path
        experiment_id: Experiment identifier
    """
    # Clean items for JSON serialization
    clean_items = []
    for item in items:
        clean_item = {}
        for key, value in item.items():
            # Skip internal fields
            if key.startswith("_"):
                continue
            # Handle non-serializable types
            if isinstance(value, (str, int, float, bool, type(None))):
                clean_item[key] = value
            elif isinstance(value, (list, dict)):
                clean_item[key] = value
            else:
                clean_item[key] = str(value)
        clean_items.append(clean_item)

    output = {
        "experiment_id": experiment_id,
        "generated_at": datetime.now().isoformat(),
        "metrics": metrics,
        "items": clean_items,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    logger.info("Exported results to %s", output_path)


def export_results_csv(
    items: List[Dict[str, Any]],
    output_path: Path,
) -> None:
    """Export results to CSV.

    Args:
        items: Evaluation items
        output_path: Output file path
    """
    if not items:
        logger.warning("No items to export to CSV")
        return

    # Determine columns
    columns = [
        "publication_id",
        "title",
        "doi",
        "pmid",
        "model_score",
        "mean_human_rating",
        "udi_rating",
        "model_reason",
        "source_type",
    ]

    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for item in items:
            row = {col: item.get(col, "") for col in columns}
            writer.writerow(row)

    logger.info("Exported CSV to %s", output_path)


def main() -> int:
    """Main entry point."""
    args = parse_args()
    setup_logging(args.verbose)

    # Generate experiment ID
    experiment_id = args.experiment_id or f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    logger.info("Starting evaluation with experiment_id: %s", experiment_id)

    # Setup output directory
    if args.output_dir:
        output_dir = args.output_dir
    else:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        output_dir = Path(f"data/outputs/scoring-eval/{timestamp}")

    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Output directory: %s", output_dir)

    # Load datasets
    items: List[Dict[str, Any]] = []

    if args.udi_file and not args.survey_file:
        logger.info("Loading unified eval dataset from %s", args.udi_file)
        items = _load_unified_dataset(args.udi_file)
        logger.info("Loaded %d items from unified dataset", len(items))
    else:
        if args.udi_file:
            logger.info("Loading Udi ground truth from %s", args.udi_file)
            udi_items = load_udi_ground_truth(args.udi_file)
            items = merge_datasets(items, udi_items)
            logger.info("Loaded %d items from Udi ground truth", len(udi_items))

        if args.survey_file:
            logger.info("Loading calibration survey from %s", args.survey_file)
            survey_items = load_calibration_survey(args.survey_file)
            items = merge_datasets(items, survey_items)
            logger.info("Loaded %d items from calibration survey", len(survey_items))

    if not items:
        logger.error("No evaluation items loaded. Provide --udi-file and/or --survey-file")
        return 1

    # Prepare items for metrics
    items = prepare_items_for_metrics(items)

    # Count labels by source
    udi_labeled = [i for i in items if i.get("udi_labels")]
    survey_labeled = [i for i in items if i.get("survey_labels")]
    logger.info("Publications with Udi labels: %d", len(udi_labeled))
    logger.info("Publications with survey labels: %d", len(survey_labeled))

    if not udi_labeled:
        logger.error("No Udi labels found in dataset. Aborting evaluation.")
        return 1

    # Apply max-items limit
    if args.max_items and len(items) > args.max_items:
        items = items[:args.max_items]
        logger.info("Limited to %d items", len(items))

    # Get model scores
    if args.run_scorer:
        # Re-score using tri-model pipeline
        logger.info("Running tri-model scorer on evaluation items...")
        items = run_tri_model_scoring(items, experiment_id, args.db_path)
    else:
        # Use existing scores from database
        if args.experiment_id and not args.tri_model_run_id:
            logger.info("Loading scores from benchmark experiment: %s", args.experiment_id)
            tri_model_results = load_tri_model_results_from_db(
                experiment_id=args.experiment_id,
                db_path=args.db_path,
            )
        else:
            logger.info("Loading existing tri-model scores from database...")
            tri_model_results = load_tri_model_results_from_db(
                run_id=args.tri_model_run_id,
                db_path=args.db_path,
            )
        items = enrich_items_with_tri_model(items, tri_model_results)

    # Count items with model scores
    items_with_model = [i for i in items if i.get("model_score") is not None]
    logger.info(
        "Found model scores for %d/%d items",
        len(items_with_model),
        len(items),
    )

    if not items_with_model:
        logger.warning(
            "No items have model scores. Run with --run-scorer to score items, "
            "or ensure tri-model results exist in database."
        )

    # Compute metrics
    metrics = compute_all_metrics(items)
    logger.info("Computed metrics: Spearman=%.3f, NDCG@5=%.3f",
                metrics.get("spearman_rho") or 0,
                metrics.get("ndcg@5") or 0)

    # Compute metrics by source
    metrics_by_source = compute_metrics_by_source(items)

    # Find top disagreements
    disagreements = find_top_disagreements(items, n=20)

    # Calibration (optional)
    calibration_info = None
    if args.calibrate and items_with_model:
        logger.info("Fitting calibration...")

        # Store pre-calibration metrics
        pre_metrics = metrics.copy()

        # Fit calibrator
        try:
            calibrator = fit_calibrator_from_items(items)

            # Validate
            if not validate_calibrator_monotonicity(calibrator):
                logger.warning("Calibration failed monotonicity validation")
            if not validate_calibrator_bounds(calibrator):
                logger.warning("Calibration failed bounds validation")

            # Apply calibration
            items = apply_calibration_to_items(items, calibrator)

            # Compute post-calibration metrics using calibrated scores
            post_items = [{**i, "model_score": i.get("calibrated_score")} for i in items]
            post_metrics = compute_all_metrics(post_items)

            # Save calibration params
            calibration_path = output_dir / "calibration_params.json"
            calibrator.save(calibration_path)

            calibration_info = {
                "pre_calibration_metrics": pre_metrics,
                "post_calibration_metrics": post_metrics,
                "mapping_table": calibrator.get_mapping_table(step=10),
                "fit_stats": calibrator.get_fit_stats(),
            }

            logger.info(
                "Calibration complete: Spearman %.3f -> %.3f",
                pre_metrics.get("spearman_rho") or 0,
                post_metrics.get("spearman_rho") or 0,
            )

        except Exception as e:
            logger.error("Calibration failed: %s", e)

    # Generate report
    report = generate_report(
        items=items,
        metrics=metrics,
        metrics_by_source=metrics_by_source,
        disagreements=disagreements,
        calibration_info=calibration_info,
        experiment_id=experiment_id,
    )

    # Write outputs
    report_path = output_dir / "eval_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info("Wrote report to %s", report_path)

    results_path = output_dir / "eval_results.json"
    export_results_json(items, metrics, results_path, experiment_id)

    if args.export_csv:
        csv_path = output_dir / "eval_results.csv"
        export_results_csv(items, csv_path)

    # Print summary
    print("\n" + "=" * 60)
    print("EVALUATION COMPLETE")
    print("=" * 60)
    print(f"Experiment ID: {experiment_id}")
    print(f"Total Items: {len(items)}")
    print(f"Items with Model Scores: {len(items_with_model)}")
    print(f"\nKey Metrics:")
    print(f"  Spearman ρ: {metrics.get('spearman_rho', 'N/A')}")
    print(f"  NDCG@5: {metrics.get('ndcg@5', 'N/A')}")
    print(f"  NDCG@10: {metrics.get('ndcg@10', 'N/A')}")
    print(f"  Recall@5 (rating=3): {metrics.get('recall@5', 'N/A')}")
    print(f"\nOutputs:")
    print(f"  Report: {report_path}")
    print(f"  Results: {results_path}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
