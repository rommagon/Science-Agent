"""Evaluation metrics for scoring evaluation.

This module provides:
- Spearman correlation (model vs human ratings)
- NDCG@K (using Udi labels as ground truth)
- Recall@K for top-rated items
"""

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def compute_spearman(
    items: List[Dict[str, Any]],
    model_score_key: str = "model_score",
    human_rating_key: str = "mean_human_rating",
) -> Dict[str, Any]:
    """Compute Spearman rank correlation between model scores and human ratings.

    Args:
        items: List of items with model scores and human ratings
        model_score_key: Key for model score field
        human_rating_key: Key for human rating field

    Returns:
        Dict with 'spearman_rho', 'p_value', 'n' (sample size)
    """
    # Filter items with both scores
    valid_items = [
        item for item in items
        if item.get(model_score_key) is not None and item.get(human_rating_key) is not None
    ]

    if len(valid_items) < 3:
        logger.warning(f"Insufficient data for Spearman correlation: {len(valid_items)} items")
        return {"spearman_rho": None, "p_value": None, "n": len(valid_items)}

    model_scores = [item[model_score_key] for item in valid_items]
    human_ratings = [item[human_rating_key] for item in valid_items]

    try:
        from scipy import stats
        rho, p_value = stats.spearmanr(model_scores, human_ratings)

        return {
            "spearman_rho": float(rho) if not math.isnan(rho) else None,
            "p_value": float(p_value) if not math.isnan(p_value) else None,
            "n": len(valid_items),
        }
    except ImportError:
        # Fallback to manual calculation if scipy not available
        logger.warning("scipy not available, using manual Spearman calculation")
        rho = _manual_spearman(model_scores, human_ratings)
        return {"spearman_rho": rho, "p_value": None, "n": len(valid_items)}


def _manual_spearman(x: List[float], y: List[float]) -> Optional[float]:
    """Manual Spearman correlation calculation without scipy.

    Args:
        x: First variable values
        y: Second variable values

    Returns:
        Spearman rho or None
    """
    n = len(x)
    if n < 3:
        return None

    # Compute ranks
    def rank_data(data):
        sorted_indices = sorted(range(len(data)), key=lambda i: data[i])
        ranks = [0.0] * len(data)
        for rank, idx in enumerate(sorted_indices, 1):
            ranks[idx] = rank
        return ranks

    rank_x = rank_data(x)
    rank_y = rank_data(y)

    # Compute Spearman rho
    d_squared = sum((rx - ry) ** 2 for rx, ry in zip(rank_x, rank_y))
    rho = 1 - (6 * d_squared) / (n * (n ** 2 - 1))

    return rho


def compute_ndcg(
    items: List[Dict[str, Any]],
    k: int,
    model_score_key: str = "model_score",
    relevance_key: str = "udi_rating",
    max_relevance: int = 3,
) -> Dict[str, Any]:
    """Compute NDCG@K (Normalized Discounted Cumulative Gain).

    Uses Udi labels as ground truth relevance.

    Args:
        items: List of items with model scores and relevance labels
        k: Cutoff for NDCG calculation
        model_score_key: Key for model score field
        relevance_key: Key for ground truth relevance field
        max_relevance: Maximum relevance value (default 3)

    Returns:
        Dict with 'ndcg', 'dcg', 'idcg', 'k', 'n_items'
    """
    # Filter items with both model score and relevance
    valid_items = [
        item for item in items
        if item.get(model_score_key) is not None and item.get(relevance_key) is not None
    ]

    if not valid_items:
        logger.warning("No valid items for NDCG calculation")
        return {"ndcg": None, "dcg": None, "idcg": None, "k": k, "n_items": 0}

    # Sort by model score (descending) to get model ranking
    model_ranked = sorted(valid_items, key=lambda x: x[model_score_key], reverse=True)

    # Calculate DCG at k
    dcg = 0.0
    for i, item in enumerate(model_ranked[:k]):
        relevance = item[relevance_key]
        # Use standard DCG formula: (2^rel - 1) / log2(i + 2)
        gain = (2 ** relevance - 1)
        discount = math.log2(i + 2)
        dcg += gain / discount

    # Calculate ideal DCG (items sorted by true relevance)
    ideal_ranked = sorted(valid_items, key=lambda x: x[relevance_key], reverse=True)
    idcg = 0.0
    for i, item in enumerate(ideal_ranked[:k]):
        relevance = item[relevance_key]
        gain = (2 ** relevance - 1)
        discount = math.log2(i + 2)
        idcg += gain / discount

    # Calculate NDCG
    ndcg = dcg / idcg if idcg > 0 else 0.0

    return {
        "ndcg": ndcg,
        "dcg": dcg,
        "idcg": idcg,
        "k": k,
        "n_items": len(valid_items),
    }


def compute_recall_at_k(
    items: List[Dict[str, Any]],
    k: int,
    model_score_key: str = "model_score",
    relevance_key: str = "udi_rating",
    relevance_threshold: int = 3,
) -> Dict[str, Any]:
    """Compute Recall@K for highly relevant items.

    Measures what fraction of items with relevance >= threshold appear in top K by model score.

    Args:
        items: List of items with model scores and relevance labels
        k: Cutoff for recall calculation
        model_score_key: Key for model score field
        relevance_key: Key for ground truth relevance field
        relevance_threshold: Minimum relevance to be considered "relevant"

    Returns:
        Dict with 'recall', 'hits', 'total_relevant', 'k'
    """
    # Filter items with both model score and relevance
    valid_items = [
        item for item in items
        if item.get(model_score_key) is not None and item.get(relevance_key) is not None
    ]

    if not valid_items:
        logger.warning("No valid items for Recall@K calculation")
        return {"recall": None, "hits": 0, "total_relevant": 0, "k": k}

    # Count total relevant items
    relevant_items = [item for item in valid_items if item[relevance_key] >= relevance_threshold]
    total_relevant = len(relevant_items)

    if total_relevant == 0:
        logger.warning("No relevant items found with threshold >= %d", relevance_threshold)
        return {"recall": None, "hits": 0, "total_relevant": 0, "k": k}

    # Get top K by model score
    model_ranked = sorted(valid_items, key=lambda x: x[model_score_key], reverse=True)
    top_k = model_ranked[:k]

    # Count hits (relevant items in top K)
    hits = sum(1 for item in top_k if item[relevance_key] >= relevance_threshold)

    recall = hits / total_relevant

    return {
        "recall": recall,
        "hits": hits,
        "total_relevant": total_relevant,
        "k": k,
    }


def compute_all_metrics(
    items: List[Dict[str, Any]],
    model_score_key: str = "model_score",
    human_rating_key: str = "mean_human_rating",
    udi_rating_key: str = "udi_rating",
) -> Dict[str, Any]:
    """Compute all evaluation metrics.

    Args:
        items: List of items with scores and ratings
        model_score_key: Key for model score field
        human_rating_key: Key for mean human rating field
        udi_rating_key: Key for Udi rating field

    Returns:
        Dict with all metrics
    """
    metrics = {}

    # Spearman correlation with mean human ratings
    spearman_result = compute_spearman(items, model_score_key, human_rating_key)
    metrics["spearman_rho"] = spearman_result["spearman_rho"]
    metrics["spearman_p_value"] = spearman_result["p_value"]
    metrics["spearman_n"] = spearman_result["n"]

    # NDCG at various K values (using Udi ratings)
    for k in [5, 10, 20]:
        ndcg_result = compute_ndcg(items, k, model_score_key, udi_rating_key)
        metrics[f"ndcg@{k}"] = ndcg_result["ndcg"]

    # Recall at various K values for top-rated items (rating == 3)
    for k in [5, 10, 20]:
        recall_result = compute_recall_at_k(items, k, model_score_key, udi_rating_key, relevance_threshold=3)
        metrics[f"recall@{k}"] = recall_result["recall"]
        if k == 5:  # Store detailed info for primary K
            metrics["recall@5_hits"] = recall_result["hits"]
            metrics["recall@5_total_relevant"] = recall_result["total_relevant"]

    return metrics


def compute_metrics_by_source(
    items: List[Dict[str, Any]],
    source_key: str = "source",
    model_score_key: str = "model_score",
    human_rating_key: str = "mean_human_rating",
    udi_rating_key: str = "udi_rating",
) -> Dict[str, Dict[str, Any]]:
    """Compute metrics broken down by source type.

    Args:
        items: List of items with scores and ratings
        source_key: Key for source field
        model_score_key: Key for model score field
        human_rating_key: Key for mean human rating field
        udi_rating_key: Key for Udi rating field

    Returns:
        Dict mapping source type to metrics
    """
    # Group items by source
    by_source: Dict[str, List[Dict]] = {}
    for item in items:
        source = item.get(source_key, "unknown")
        # Normalize source to category
        source_lower = str(source).lower()
        if "pubmed" in source_lower or "ncbi" in source_lower:
            category = "pubmed"
        elif "medrxiv" in source_lower or "biorxiv" in source_lower:
            category = "preprint"
        elif "nature" in source_lower or "lancet" in source_lower or "nejm" in source_lower:
            category = "journal"
        else:
            category = "other"

        if category not in by_source:
            by_source[category] = []
        by_source[category].append(item)

    # Compute metrics per source
    results = {}
    for source, source_items in by_source.items():
        results[source] = compute_all_metrics(
            source_items,
            model_score_key,
            human_rating_key,
            udi_rating_key,
        )
        results[source]["n_items"] = len(source_items)

    return results


def find_top_disagreements(
    items: List[Dict[str, Any]],
    n: int = 20,
    model_score_key: str = "model_score",
    human_rating_key: str = "mean_human_rating",
) -> List[Dict[str, Any]]:
    """Find items with largest disagreement between model and human ratings.

    Args:
        items: List of items with scores and ratings
        n: Number of top disagreements to return
        model_score_key: Key for model score field
        human_rating_key: Key for mean human rating field

    Returns:
        List of top N disagreement items with error info
    """
    # Filter items with both scores
    valid_items = [
        item for item in items
        if item.get(model_score_key) is not None and item.get(human_rating_key) is not None
    ]

    if not valid_items:
        return []

    # Compute error for each item
    # Normalize model_score (0-100) to same scale as human_rating (0-3)
    for item in valid_items:
        model_score = item[model_score_key]
        human_rating = item[human_rating_key]

        # Convert model score to 0-3 scale for comparison
        model_scaled = model_score / 100.0 * 3.0
        error = abs(model_scaled - human_rating)
        item["_error"] = error
        item["_model_scaled"] = model_scaled

    # Sort by error (descending)
    sorted_items = sorted(valid_items, key=lambda x: x["_error"], reverse=True)

    # Return top N with relevant fields
    top_disagreements = []
    for item in sorted_items[:n]:
        disagreement = {
            "title": item.get("title", ""),
            "publication_id": item.get("publication_id"),
            "doi": item.get("doi"),
            "mean_human_rating": item[human_rating_key],
            "model_score": item[model_score_key],
            "model_score_scaled_0_3": round(item["_model_scaled"], 2),
            "absolute_error": round(item["_error"], 2),
            "model_reason": item.get("model_reason", ""),
            "human_rationale": _get_human_rationale(item),
            "udi_rating": item.get("udi_rating"),
        }
        top_disagreements.append(disagreement)

    return top_disagreements


def _get_human_rationale(item: Dict[str, Any]) -> str:
    """Extract human rationale from item labels.

    Args:
        item: Item with human_labels

    Returns:
        Combined rationale string
    """
    labels = item.get("human_labels", [])
    rationales = []
    for label in labels:
        rationale = label.get("rationale")
        if rationale:
            source = label.get("source", "unknown")
            rater = label.get("rater", "")
            prefix = f"[{source}"
            if rater:
                prefix += f"/{rater}"
            prefix += "]"
            rationales.append(f"{prefix} {rationale}")
    return " | ".join(rationales) if rationales else ""


def score_to_rating(score: float, thresholds: Tuple[int, int, int] = (25, 50, 75)) -> int:
    """Convert 0-100 score to 0-3 rating using thresholds.

    Args:
        score: Score on 0-100 scale
        thresholds: (threshold_1, threshold_2, threshold_3) for ratings

    Returns:
        Rating on 0-3 scale
    """
    t1, t2, t3 = thresholds
    if score < t1:
        return 0
    elif score < t2:
        return 1
    elif score < t3:
        return 2
    else:
        return 3


def rating_to_score_range(rating: int) -> Tuple[int, int]:
    """Convert 0-3 rating to expected score range.

    Args:
        rating: Rating on 0-3 scale

    Returns:
        (min_score, max_score) tuple
    """
    ranges = {
        0: (0, 24),
        1: (25, 49),
        2: (50, 74),
        3: (75, 100),
    }
    return ranges.get(rating, (0, 100))


def compute_classification_accuracy(
    items: List[Dict[str, Any]],
    model_score_key: str = "model_score",
    human_rating_key: str = "mean_human_rating",
    thresholds: Tuple[int, int, int] = (25, 50, 75),
) -> Dict[str, Any]:
    """Compute classification accuracy when treating as 4-class problem.

    Args:
        items: List of items with scores and ratings
        model_score_key: Key for model score field
        human_rating_key: Key for mean human rating field (0-3)
        thresholds: Score thresholds for classification

    Returns:
        Dict with accuracy, confusion matrix info
    """
    valid_items = [
        item for item in items
        if item.get(model_score_key) is not None and item.get(human_rating_key) is not None
    ]

    if not valid_items:
        return {"accuracy": None, "n": 0}

    correct = 0
    confusion = [[0, 0, 0, 0] for _ in range(4)]  # 4x4 matrix

    for item in valid_items:
        model_score = item[model_score_key]
        human_rating = round(item[human_rating_key])
        human_rating = max(0, min(3, human_rating))

        predicted = score_to_rating(model_score, thresholds)

        if predicted == human_rating:
            correct += 1

        confusion[human_rating][predicted] += 1

    accuracy = correct / len(valid_items)

    return {
        "accuracy": accuracy,
        "n": len(valid_items),
        "correct": correct,
        "confusion_matrix": confusion,
    }
