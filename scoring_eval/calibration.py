"""Calibration layer for relevancy scoring.

This module provides isotonic regression calibration to map
model scores (0-100) to expected human ratings (0-3).
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class IsotonicCalibrator:
    """Isotonic regression calibrator for score mapping.

    Maps model scores (0-100) to calibrated scores that better
    align with human ratings (0-3 scale).
    """

    def __init__(self):
        """Initialize calibrator."""
        self._is_fitted = False
        self._x_thresholds: List[float] = []
        self._y_values: List[float] = []
        self._fit_stats: Dict[str, Any] = {}

    @property
    def is_fitted(self) -> bool:
        """Check if calibrator has been fitted."""
        return self._is_fitted

    def fit(
        self,
        model_scores: List[float],
        human_ratings: List[float],
        output_scale: str = "0_100",
    ) -> "IsotonicCalibrator":
        """Fit isotonic regression on model scores vs human ratings.

        Args:
            model_scores: Model scores (0-100 scale)
            human_ratings: Human ratings (0-3 scale)
            output_scale: Output scale - "0_100" or "0_3"

        Returns:
            Self for chaining
        """
        if len(model_scores) != len(human_ratings):
            raise ValueError("model_scores and human_ratings must have same length")

        if len(model_scores) < 5:
            raise ValueError("Need at least 5 samples for calibration")

        # Convert to numpy arrays
        x = np.array(model_scores, dtype=float)
        y = np.array(human_ratings, dtype=float)

        # Scale human ratings to 0-100 if needed
        if output_scale == "0_100":
            y = y / 3.0 * 100.0

        # Sort by x values
        sort_idx = np.argsort(x)
        x_sorted = x[sort_idx]
        y_sorted = y[sort_idx]

        # Fit isotonic regression (using PAVA algorithm)
        y_isotonic = self._fit_pava(x_sorted, y_sorted)

        # Extract unique thresholds
        self._x_thresholds = []
        self._y_values = []

        prev_y = None
        for xi, yi in zip(x_sorted, y_isotonic):
            if prev_y is None or yi != prev_y:
                self._x_thresholds.append(float(xi))
                self._y_values.append(float(yi))
                prev_y = yi

        # Ensure we cover the full range
        if self._x_thresholds[0] > 0:
            self._x_thresholds.insert(0, 0.0)
            self._y_values.insert(0, self._y_values[0])
        if self._x_thresholds[-1] < 100:
            self._x_thresholds.append(100.0)
            self._y_values.append(self._y_values[-1])

        # Store fit statistics
        self._fit_stats = {
            "n_samples": len(model_scores),
            "n_thresholds": len(self._x_thresholds),
            "output_scale": output_scale,
            "x_min": float(np.min(x)),
            "x_max": float(np.max(x)),
            "y_min": float(np.min(y)),
            "y_max": float(np.max(y)),
        }

        self._is_fitted = True
        logger.info(
            "Fitted isotonic calibrator on %d samples with %d thresholds",
            len(model_scores),
            len(self._x_thresholds),
        )

        return self

    def _fit_pava(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Pool Adjacent Violators Algorithm for isotonic regression.

        Args:
            x: Sorted x values
            y: Corresponding y values

        Returns:
            Isotonic y values
        """
        n = len(y)
        y_isotonic = y.copy()

        # Iterate until monotonic
        iterations = 0
        max_iterations = n * 2

        while iterations < max_iterations:
            changed = False

            # Forward pass - pool violators
            i = 0
            while i < n - 1:
                # Find block of consecutive points that violate monotonicity
                if y_isotonic[i] > y_isotonic[i + 1]:
                    # Find end of violating block
                    j = i + 1
                    while j < n - 1 and y_isotonic[j] > y_isotonic[j + 1]:
                        j += 1

                    # Pool the block
                    block_mean = np.mean(y_isotonic[i:j + 1])
                    y_isotonic[i:j + 1] = block_mean
                    changed = True
                    i = j + 1
                else:
                    i += 1

            if not changed:
                break
            iterations += 1

        return y_isotonic

    def transform(self, model_score: float) -> float:
        """Transform a single model score using fitted calibration.

        Args:
            model_score: Raw model score (0-100)

        Returns:
            Calibrated score
        """
        if not self._is_fitted:
            raise ValueError("Calibrator must be fitted before transform")

        # Clamp input to valid range
        score = max(0.0, min(100.0, float(model_score)))

        # Find appropriate threshold bracket
        for i in range(len(self._x_thresholds) - 1):
            if score <= self._x_thresholds[i + 1]:
                # Linear interpolation within bracket
                x0, x1 = self._x_thresholds[i], self._x_thresholds[i + 1]
                y0, y1 = self._y_values[i], self._y_values[i + 1]

                if x1 == x0:
                    return y0

                t = (score - x0) / (x1 - x0)
                return y0 + t * (y1 - y0)

        # Fallback to last value
        return self._y_values[-1]

    def transform_batch(self, model_scores: List[float]) -> List[float]:
        """Transform a batch of model scores.

        Args:
            model_scores: List of raw model scores

        Returns:
            List of calibrated scores
        """
        return [self.transform(s) for s in model_scores]

    def save(self, path: Path) -> None:
        """Save calibration parameters to JSON file.

        Args:
            path: Output file path
        """
        if not self._is_fitted:
            raise ValueError("Calibrator must be fitted before saving")

        data = {
            "x_thresholds": self._x_thresholds,
            "y_values": self._y_values,
            "fit_stats": self._fit_stats,
            "version": "1.0",
        }

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        logger.info("Saved calibration parameters to %s", path)

    def load(self, path: Path) -> "IsotonicCalibrator":
        """Load calibration parameters from JSON file.

        Args:
            path: Input file path

        Returns:
            Self for chaining
        """
        path = Path(path)
        if not path.exists():
            raise ValueError(f"Calibration file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self._x_thresholds = data["x_thresholds"]
        self._y_values = data["y_values"]
        self._fit_stats = data.get("fit_stats", {})
        self._is_fitted = True

        logger.info("Loaded calibration parameters from %s", path)
        return self

    def get_mapping_table(self, step: int = 10) -> List[Tuple[int, float]]:
        """Get human-readable mapping table.

        Args:
            step: Step size for table (default 10)

        Returns:
            List of (raw_score, calibrated_score) tuples
        """
        if not self._is_fitted:
            raise ValueError("Calibrator must be fitted")

        table = []
        for score in range(0, 101, step):
            calibrated = self.transform(score)
            table.append((score, round(calibrated, 1)))

        return table

    def get_fit_stats(self) -> Dict[str, Any]:
        """Get fit statistics.

        Returns:
            Dict with fit statistics
        """
        return self._fit_stats.copy()


def fit_calibrator_from_items(
    items: List[Dict[str, Any]],
    model_score_key: str = "model_score",
    human_rating_key: str = "mean_human_rating",
    output_scale: str = "0_100",
) -> IsotonicCalibrator:
    """Fit calibrator from evaluation items.

    Args:
        items: List of items with model scores and human ratings
        model_score_key: Key for model score field
        human_rating_key: Key for human rating field
        output_scale: Output scale - "0_100" or "0_3"

    Returns:
        Fitted calibrator
    """
    # Extract valid pairs
    pairs = [
        (item[model_score_key], item[human_rating_key])
        for item in items
        if item.get(model_score_key) is not None and item.get(human_rating_key) is not None
    ]

    if not pairs:
        raise ValueError("No valid score/rating pairs found")

    model_scores, human_ratings = zip(*pairs)

    calibrator = IsotonicCalibrator()
    calibrator.fit(list(model_scores), list(human_ratings), output_scale)

    return calibrator


def apply_calibration_to_items(
    items: List[Dict[str, Any]],
    calibrator: IsotonicCalibrator,
    model_score_key: str = "model_score",
    calibrated_key: str = "calibrated_score",
) -> List[Dict[str, Any]]:
    """Apply calibration to items.

    Args:
        items: List of items with model scores
        calibrator: Fitted calibrator
        model_score_key: Key for model score field
        calibrated_key: Key for calibrated score output

    Returns:
        Items with calibrated scores added
    """
    result = []
    for item in items:
        new_item = item.copy()
        model_score = item.get(model_score_key)

        if model_score is not None:
            new_item[calibrated_key] = calibrator.transform(model_score)
        else:
            new_item[calibrated_key] = None

        result.append(new_item)

    return result


def validate_calibrator_monotonicity(calibrator: IsotonicCalibrator) -> bool:
    """Validate that calibrator produces monotonic outputs.

    Args:
        calibrator: Fitted calibrator

    Returns:
        True if monotonic, False otherwise
    """
    if not calibrator.is_fitted:
        return False

    prev = None
    for score in range(0, 101):
        calibrated = calibrator.transform(score)
        if prev is not None and calibrated < prev - 0.001:  # Allow small floating point errors
            logger.warning(
                "Monotonicity violation: score %d -> %.2f, but score %d -> %.2f",
                score - 1, prev, score, calibrated
            )
            return False
        prev = calibrated

    return True


def validate_calibrator_bounds(
    calibrator: IsotonicCalibrator,
    output_scale: str = "0_100",
) -> bool:
    """Validate that calibrator outputs are within expected bounds.

    Args:
        calibrator: Fitted calibrator
        output_scale: Expected output scale

    Returns:
        True if within bounds, False otherwise
    """
    if not calibrator.is_fitted:
        return False

    max_val = 100.0 if output_scale == "0_100" else 3.0

    for score in range(0, 101):
        calibrated = calibrator.transform(score)
        if calibrated < 0 or calibrated > max_val:
            logger.warning(
                "Bounds violation: score %d -> %.2f (expected 0-%.0f)",
                score, calibrated, max_val
            )
            return False

    return True
