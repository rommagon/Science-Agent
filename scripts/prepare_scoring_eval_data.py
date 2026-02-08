#!/usr/bin/env python3
"""Prepare merged scoring evaluation dataset from UDI and survey files."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def normalize_title(title: Optional[str]) -> str:
    """Normalize title for matching."""
    if not title:
        return ""
    lowered = title.lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (ValueError, TypeError):
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


def map_rating_to_0_3(raw_rating: Any) -> Optional[int]:
    """Map 0-100 rating to 0-3."""
    rating = _coerce_int(raw_rating)
    if rating is None:
        return None

    if 0 <= rating <= 3:
        return rating
    if 0 <= rating <= 100:
        if rating <= 24:
            return 0
        if rating <= 49:
            return 1
        if rating <= 74:
            return 2
        return 3
    return None


def _normalize_keyed_record(record: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k).strip().lower(): v for k, v in record.items()}


def _first_value(record: Dict[str, Any], keys: Iterable[str]) -> Optional[Any]:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def _extract_survey_fields(record: Dict[str, Any]) -> Dict[str, Optional[Any]]:
    keys = _normalize_keyed_record(record)
    return {
        "publication_id": _first_value(keys, ["publication_id"]),
        "title": _first_value(keys, ["title"]),
        "final_relevancy_score": _first_value(keys, ["final_relevancy_score"]),
        "human_score": _first_value(keys, ["human_score"]),
        "reasoning": _first_value(keys, ["reasoning"]),
        "evaluator": _first_value(keys, ["evaluator"]),
        "confidence": _first_value(keys, ["confidence"]),
    }


def _extract_udi_fields(record: Dict[str, Any]) -> Dict[str, Optional[Any]]:
    keys = _normalize_keyed_record(record)
    return {
        "title": _first_value(keys, ["title"]),
        "rank": _first_value(keys, ["rank"]),
        "explanation": _first_value(keys, ["explanation"]),
    }


@dataclass
class MergeSummary:
    num_pubs_total: int
    num_udi_labels: int
    num_survey_labels: int
    num_matched_by_title: int
    label_distribution: Dict[int, int]
    average_human_score_per_publication: Optional[float]


def _load_records(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return [dict(row) for row in reader]
    if path.suffix.lower() == ".json":
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("data", "records", "items"):
                if key in data and isinstance(data[key], list):
                    return data[key]
            return [data]
        raise ValueError("Unsupported JSON structure")
    raise ValueError("Unsupported file type; expected .csv or .json")


def _merge_publication(existing: Dict[str, Any], incoming_pub: Dict[str, Any]) -> None:
    for field in ("publication_id", "title"):
        if not existing.get(field) and incoming_pub.get(field):
            existing[field] = incoming_pub[field]


def prepare_dataset(
    udi_records: List[Dict[str, Any]],
    survey_records: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], MergeSummary]:
    pubs: List[Dict[str, Any]] = []
    index_pub_id: Dict[str, Dict[str, Any]] = {}
    index_title: Dict[str, Dict[str, Any]] = {}

    matched_by_title = 0
    num_udi_labels = 0
    num_survey_labels = 0
    label_distribution = {0: 0, 1: 0, 2: 0, 3: 0}
    survey_scores_by_pub: Dict[str, List[int]] = {}

    def insert_label(pub_fields: Dict[str, Optional[str]], label: Dict[str, Any], match_title_only: bool) -> None:
        nonlocal matched_by_title

        pub_id = str(pub_fields.get("publication_id") or "").strip()
        title_norm = normalize_title(pub_fields.get("title"))

        target = None
        match_type = None

        if pub_id and pub_id in index_pub_id:
            target = index_pub_id[pub_id]
        elif title_norm and title_norm in index_title:
            target = index_title[title_norm]
            match_type = "title"

        if target is None:
            target = {
                "publication_id": pub_fields.get("publication_id"),
                "title": pub_fields.get("title"),
                "labels": [],
            }
            pubs.append(target)
        else:
            _merge_publication(target, pub_fields)
            if match_type == "title" and match_title_only:
                matched_by_title += 1

        target["labels"].append(label)

        if pub_id:
            index_pub_id.setdefault(pub_id, target)
        if title_norm:
            index_title.setdefault(title_norm, target)

    for record in survey_records:
        survey = _extract_survey_fields(record)
        evaluator = str(survey.get("evaluator") or "").strip()
        if evaluator.lower() in {"rom", "rom z"}:
            continue

        pub_fields = {
            "publication_id": survey.get("publication_id"),
            "title": survey.get("title"),
        }

        rating_raw = survey.get("human_score")
        rating_0_3 = map_rating_to_0_3(rating_raw)
        label = {
            "source": "calibration_survey",
            "rater": evaluator or None,
            "rating_0_3": rating_0_3,
            "rating_raw": rating_raw,
            "rationale": survey.get("reasoning"),
            "confidence": survey.get("confidence"),
        }

        num_survey_labels += 1
        if isinstance(rating_0_3, int) and rating_0_3 in label_distribution:
            label_distribution[rating_0_3] += 1

        insert_label(pub_fields, label, match_title_only=True)

        if rating_raw is not None:
            rating_int = _coerce_int(rating_raw)
            if rating_int is not None:
                survey_key = pub_fields.get("publication_id") or normalize_title(pub_fields.get("title"))
                if survey_key:
                    survey_scores_by_pub.setdefault(str(survey_key), []).append(rating_int)

    for record in udi_records:
        udi = _extract_udi_fields(record)
        pub_fields = {
            "publication_id": None,
            "title": udi.get("title"),
        }
        rating_raw = udi.get("rank")
        rating_0_3 = map_rating_to_0_3(rating_raw)
        label = {
            "source": "udi_ground_truth",
            "rater": "udi",
            "rating_0_3": rating_0_3,
            "rating_raw": rating_raw,
            "rationale": udi.get("explanation"),
            "confidence": "high",
        }

        num_udi_labels += 1
        if isinstance(rating_0_3, int) and rating_0_3 in label_distribution:
            label_distribution[rating_0_3] += 1

        insert_label(pub_fields, label, match_title_only=True)

    avg_per_pub = None
    if survey_scores_by_pub:
        per_pub_avgs = [sum(scores) / len(scores) for scores in survey_scores_by_pub.values()]
        avg_per_pub = sum(per_pub_avgs) / len(per_pub_avgs)

    summary = MergeSummary(
        num_pubs_total=len(pubs),
        num_udi_labels=num_udi_labels,
        num_survey_labels=num_survey_labels,
        num_matched_by_title=matched_by_title,
        label_distribution=label_distribution,
        average_human_score_per_publication=avg_per_pub,
    )

    return pubs, summary


def _write_outputs(out_dir: Path, dataset: List[Dict[str, Any]], summary: MergeSummary) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "eval_dataset.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)

    csv_path = out_dir / "eval_dataset.csv"
    fieldnames = [
        "publication_id",
        "title",
        "label_source",
        "label_rater",
        "label_rating_0_3",
        "label_rating_raw",
        "label_rationale",
        "label_confidence",
    ]

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for pub in dataset:
            for label in pub.get("labels", []):
                writer.writerow({
                    "publication_id": pub.get("publication_id"),
                    "title": pub.get("title"),
                    "label_source": label.get("source"),
                    "label_rater": label.get("rater"),
                    "label_rating_0_3": label.get("rating_0_3"),
                    "label_rating_raw": label.get("rating_raw"),
                    "label_rationale": label.get("rationale"),
                    "label_confidence": label.get("confidence"),
                })

    summary_path = out_dir / "summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump({
            "num_publications": summary.num_pubs_total,
            "num_udi_labels": summary.num_udi_labels,
            "num_survey_labels": summary.num_survey_labels,
            "num_matched_by_title": summary.num_matched_by_title,
            "rating_distribution_0_3": {str(k): v for k, v in summary.label_distribution.items()},
            "average_human_score_per_publication": summary.average_human_score_per_publication,
        }, f, indent=2, ensure_ascii=False)


def _print_summary(summary: MergeSummary, out_dir: Path) -> None:
    print("Scoring eval dataset prepared")
    print(f"Output dir: {out_dir}")
    print(f"Publications: {summary.num_pubs_total}")
    print(f"UDI labels: {summary.num_udi_labels}")
    print(f"Survey labels: {summary.num_survey_labels}")
    print(f"Matched by title: {summary.num_matched_by_title}")
    print("Rating distribution (0-3): " + ", ".join(
        f"{k}={summary.label_distribution.get(k, 0)}" for k in (0, 1, 2, 3)
    ))
    print(f"Avg human score per publication: {summary.average_human_score_per_publication}")


def enrich_items_from_db(items: List[Dict[str, Any]], db_path: str) -> List[Dict[str, Any]]:
    """Enrich items with metadata from the local SQLite publications table."""
    db_file = Path(db_path)
    if not db_file.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = """
        SELECT
            id,
            doi,
            pmid,
            url,
            canonical_url,
            published_date,
            source,
            venue,
            venue_name,
            raw_text,
            summary
        FROM publications
        WHERE id = ?
    """

    enriched = []
    for item in items:
        pub_id = item.get("publication_id")
        if not pub_id:
            enriched.append(item)
            continue

        cursor.execute(query, (pub_id,))
        row = cursor.fetchone()
        if not row:
            enriched.append(item)
            continue

        updated = item.copy()
        abstract = row["raw_text"] or row["summary"]
        updated.setdefault("doi", row["doi"])
        updated.setdefault("pmid", row["pmid"])
        updated.setdefault("url", row["url"])
        updated.setdefault("canonical_url", row["canonical_url"])
        updated.setdefault("published_date", row["published_date"])
        updated.setdefault("source", row["source"])
        updated.setdefault("venue", row["venue"] or row["venue_name"])
        if abstract:
            updated.setdefault("abstract", abstract)

        enriched.append(updated)

    conn.close()
    return enriched


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare merged scoring eval dataset")
    parser.add_argument(
        "--survey-file",
        default="scoring_eval_data/raw/calibration_survey.csv",
        help="Path to calibration survey CSV",
    )
    parser.add_argument(
        "--udi-file",
        default="scoring_eval_data/raw/udi_ground_truth.csv",
        help="Path to UDI ground truth CSV",
    )
    parser.add_argument(
        "--out-dir",
        default="scoring_eval_data/clean",
        help="Output directory for cleaned dataset",
    )
    parser.add_argument(
        "--db-path",
        default="data/db/acitrack.db",
        help="Path to SQLite database for enrichment",
    )
    args = parser.parse_args()

    udi_path = Path(args.udi_file)
    survey_path = Path(args.survey_file)
    out_dir = Path(args.out_dir)

    udi_records = _load_records(udi_path)
    survey_records = _load_records(survey_path)

    dataset, summary = prepare_dataset(udi_records, survey_records)
    dataset = enrich_items_from_db(dataset, args.db_path)
    _write_outputs(out_dir, dataset, summary)
    _print_summary(summary, out_dir)


if __name__ == "__main__":
    main()
