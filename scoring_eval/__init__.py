"""Scoring evaluation framework for relevancy scoring calibration.

This package provides tools for:
- Loading and normalizing ground truth datasets (Udi's rankings, calibration surveys)
- Running evaluation harness against tri-model pipeline
- Computing metrics (Spearman correlation, NDCG, Recall)
- Calibration layer using isotonic regression
"""

from .datasets import (
    load_udi_ground_truth,
    load_calibration_survey,
    normalize_to_canonical,
    merge_datasets,
    match_publications,
)
from .metrics import (
    compute_spearman,
    compute_ndcg,
    compute_recall_at_k,
    compute_all_metrics,
)

__all__ = [
    "load_udi_ground_truth",
    "load_calibration_survey",
    "normalize_to_canonical",
    "merge_datasets",
    "match_publications",
    "compute_spearman",
    "compute_ndcg",
    "compute_recall_at_k",
    "compute_all_metrics",
]
