#!/usr/bin/env python3
"""Evaluate llm_relevancy V3 against human-labeled ground truth.

This script directly benchmarks `mcp_server.llm_relevancy.score_relevancy`
using the merged evaluation dataset at `scoring_eval_data/clean/eval_dataset.json`.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_server.llm_relevancy import score_relevancy, SCORING_VERSION
from scoring_eval.datasets import normalize_title
from scoring_eval.metrics import compute_all_metrics, find_top_disagreements


logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate llm_relevancy V3 on ground-truth dataset")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("scoring_eval_data/clean/eval_dataset.json"),
        help="Path to merged eval dataset JSON",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="data/db/acitrack.db",
        help="Path to SQLite database with publication metadata",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: /tmp/llm_relevancy_v3_eval_<timestamp>)",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Optional limit on number of dataset items",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Optional run_id used for caching/storing events",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Optional model override (sets SPOTITEARLY_LLM_MODEL for this process)",
    )
    parser.add_argument(
        "--store-to-db",
        action="store_true",
        help="Store scoring events to DB",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose logging",
    )
    return parser.parse_args()


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def _load_dataset(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Dataset file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Dataset must be a list of publication entries")
    return data


def _majority_rating(labels: List[Dict[str, Any]]) -> Optional[int]:
    ratings = [l.get("rating_0_3") for l in labels if l.get("rating_0_3") is not None]
    ratings = [int(r) for r in ratings]
    if not ratings:
        return None
    counts = Counter(ratings)
    # Deterministic tie-break: higher rating wins on tie
    return sorted(counts.items(), key=lambda x: (-x[1], -x[0]))[0][0]


def _mean_rating(labels: List[Dict[str, Any]]) -> Optional[float]:
    ratings = [l.get("rating_0_3") for l in labels if l.get("rating_0_3") is not None]
    ratings = [float(r) for r in ratings]
    if not ratings:
        return None
    return sum(ratings) / len(ratings)


def _fetch_publication_metadata(db_path: str) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Build lookup maps by publication id and normalized title."""
    by_id: Dict[str, Dict[str, Any]] = {}
    by_title: Dict[str, Dict[str, Any]] = {}

    db_file = Path(db_path)
    if not db_file.exists():
        logger.warning("DB file not found at %s. Continuing without metadata enrichment.", db_path)
        return by_id, by_title

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, title, source, url, raw_text, summary
        FROM publications
        """
    )
    rows = cur.fetchall()
    conn.close()

    for r in rows:
        item = {
            "id": r["id"],
            "title": r["title"] or "",
            "source": r["source"] or "",
            "url": r["url"] or "",
            "raw_text": r["raw_text"] or "",
            "summary": r["summary"] or "",
        }
        pid = item["id"] or ""
        if pid:
            by_id[pid] = item

        tnorm = normalize_title(item["title"])
        if tnorm and tnorm not in by_title:
            by_title[tnorm] = item

    logger.info("Loaded %d publication rows from DB for enrichment", len(rows))
    return by_id, by_title


def _build_eval_items(dataset: List[Dict[str, Any]], db_by_id: Dict[str, Dict[str, Any]], db_by_title: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    eval_items: List[Dict[str, Any]] = []
    matched_by_id = 0
    matched_by_title = 0

    for row in dataset:
        publication_id = row.get("publication_id")
        title = row.get("title", "")
        labels = row.get("labels", [])

        pub_meta = None
        if publication_id and publication_id in db_by_id:
            pub_meta = db_by_id[publication_id]
            matched_by_id += 1
        else:
            tnorm = normalize_title(title)
            if tnorm and tnorm in db_by_title:
                pub_meta = db_by_title[tnorm]
                matched_by_title += 1

        survey_labels = [l for l in labels if l.get("source") == "calibration_survey"]
        udi_labels = [l for l in labels if l.get("source") == "udi_ground_truth"]

        eval_items.append(
            {
                "publication_id": publication_id,
                "title": title,
                "labels": labels,
                "survey_labels": survey_labels,
                "udi_labels": udi_labels,
                "mean_human_rating": _mean_rating(survey_labels),
                "udi_rating": _majority_rating(udi_labels),
                "source": (pub_meta or {}).get("source", ""),
                "url": (pub_meta or {}).get("url", ""),
                "raw_text": (pub_meta or {}).get("raw_text", ""),
                "summary": (pub_meta or {}).get("summary", ""),
            }
        )

    logger.info(
        "Metadata matches: publication_id=%d, title=%d, unmatched=%d",
        matched_by_id,
        matched_by_title,
        len(eval_items) - matched_by_id - matched_by_title,
    )
    return eval_items


def _score_items(
    items: List[Dict[str, Any]],
    run_id: str,
    db_path: str,
    store_to_db: bool,
) -> None:
    for idx, item in enumerate(items, 1):
        logger.info("Scoring %d/%d: %s", idx, len(items), item["title"][:90])

        score_item = {
            "id": item.get("publication_id") or f"eval-{idx}",
            "title": item.get("title", ""),
            "source": item.get("source", ""),
            "url": item.get("url", ""),
            "raw_text": item.get("raw_text") or item.get("summary") or "",
            "summary": item.get("summary") or "",
        }

        result = score_relevancy(
            score_item,
            run_id=run_id,
            mode="scoring-eval",
            store_to_db=store_to_db,
            db_path=db_path,
        )

        item["model_score"] = result.get("relevancy_score")
        item["model_reason"] = result.get("relevancy_reason")
        item["model_confidence"] = result.get("confidence")
        item["model_signals"] = result.get("signals", {})
        item["scoring_version"] = result.get("scoring_version")
        item["scoring_model"] = result.get("scoring_model")
        if result.get("error"):
            item["model_error"] = result["error"]


def _write_outputs(output_dir: Path, items: List[Dict[str, Any]], metrics: Dict[str, Any], disagreements: List[Dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    results_json = output_dir / "eval_results.json"
    with open(results_json, "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at": datetime.now().isoformat(),
                "metrics": metrics,
                "items": items,
                "top_disagreements": disagreements,
            },
            f,
            indent=2,
        )

    results_csv = output_dir / "eval_results.csv"
    fieldnames = [
        "publication_id",
        "title",
        "model_score",
        "mean_human_rating",
        "udi_rating",
        "model_confidence",
        "scoring_version",
        "scoring_model",
        "model_reason",
    ]
    with open(results_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for i in items:
            writer.writerow(i)

    report_path = output_dir / "eval_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# LLM Relevancy V3 Evaluation Report\n\n")
        f.write(f"- Generated: {datetime.now().isoformat()}\n")
        f.write(f"- Items: {len(items)}\n")
        f.write(f"- Spearman: {metrics.get('spearman_rho')}\n")
        f.write(f"- NDCG@5: {metrics.get('ndcg@5')}\n")
        f.write(f"- NDCG@10: {metrics.get('ndcg@10')}\n")
        f.write(f"- Recall@5 (rating=3): {metrics.get('recall@5')}\n")
        f.write(f"- Recall@10 (rating=3): {metrics.get('recall@10')}\n")
        f.write(f"- Recall@20 (rating=3): {metrics.get('recall@20')}\n\n")
        f.write("## Top Disagreements\n\n")
        for i, d in enumerate(disagreements[:20], 1):
            f.write(f"{i}. {d.get('title', '')}\n")
            f.write(f"   - mean_human_rating: {d.get('mean_human_rating')}\n")
            f.write(f"   - model_score: {d.get('model_score')}\n")
            f.write(f"   - absolute_error: {d.get('absolute_error')}\n")


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)

    if args.model:
        os.environ["SPOTITEARLY_LLM_MODEL"] = args.model
        logger.info("Using model override from --model: %s", args.model)

    if not os.getenv("SPOTITEARLY_LLM_API_KEY"):
        logger.error("SPOTITEARLY_LLM_API_KEY is not set.")
        return 1

    dataset = _load_dataset(args.dataset)
    if args.max_items:
        dataset = dataset[: args.max_items]

    db_by_id, db_by_title = _fetch_publication_metadata(args.db_path)
    items = _build_eval_items(dataset, db_by_id, db_by_title)

    run_id = args.run_id or f"llm-relevancy-v3-eval-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    logger.info("Scoring with run_id=%s (scoring_version=%s)", run_id, SCORING_VERSION)
    _score_items(items, run_id=run_id, db_path=args.db_path, store_to_db=args.store_to_db)

    metrics = compute_all_metrics(items)
    disagreements = find_top_disagreements(items, n=20)

    output_dir = args.output_dir or Path(f"/tmp/llm_relevancy_v3_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    _write_outputs(output_dir, items, metrics, disagreements)

    print("\n" + "=" * 60)
    print("LLM RELEVANCY V3 EVALUATION COMPLETE")
    print("=" * 60)
    print(f"Run ID: {run_id}")
    print(f"Items: {len(items)}")
    print(f"Spearman ρ: {metrics.get('spearman_rho')}")
    print(f"NDCG@5: {metrics.get('ndcg@5')}")
    print(f"Recall@5 (rating=3): {metrics.get('recall@5')}")
    print(f"Outputs: {output_dir}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
