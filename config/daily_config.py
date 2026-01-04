"""Configuration for daily pipeline runs."""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Tuple


@dataclass
class RunContext:
    """Context for a daily pipeline run.

    Attributes:
        run_id: Date-based identifier in YYYY-MM-DD format (UTC)
        window_start: Start of lookback window (UTC)
        window_end: End of lookback window (UTC)
        lookback_hours: Number of hours in the lookback window
    """
    run_id: str
    window_start: datetime
    window_end: datetime
    lookback_hours: int


# Pipeline configuration
RUN_FREQUENCY = os.getenv("ACITRACK_RUN_FREQUENCY", "daily")
LOOKBACK_HOURS = int(os.getenv("ACITRACK_LOOKBACK_HOURS", "48"))

# Feature flags
WRITE_SHEETS = os.getenv("ACITRACK_WRITE_SHEETS", "false").lower() in ("true", "1", "yes")


def compute_run_context(lookback_hours: int = None) -> RunContext:
    """Compute the run context for a daily pipeline execution.

    Generates a date-based run_id and calculates the time window for
    ingesting publications. Uses a rolling lookback window to avoid
    missing delayed postings.

    Args:
        lookback_hours: Number of hours to look back from now.
                       Defaults to LOOKBACK_HOURS env var or 48.

    Returns:
        RunContext with run_id and time window boundaries (UTC)

    Example:
        >>> ctx = compute_run_context(lookback_hours=48)
        >>> ctx.run_id
        '2026-01-03'
        >>> ctx.window_start
        datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        >>> ctx.window_end
        datetime(2026, 1, 3, 12, 0, 0, tzinfo=timezone.utc)
    """
    if lookback_hours is None:
        lookback_hours = LOOKBACK_HOURS

    # Get current time in UTC
    now_utc = datetime.now(timezone.utc)

    # Generate run_id as UTC date string (YYYY-MM-DD)
    run_id = now_utc.strftime("%Y-%m-%d")

    # Calculate time window
    window_end = now_utc
    window_start = now_utc - timedelta(hours=lookback_hours)

    return RunContext(
        run_id=run_id,
        window_start=window_start,
        window_end=window_end,
        lookback_hours=lookback_hours,
    )


def get_legacy_run_id() -> str:
    """Generate legacy run_id format for backward compatibility.

    Returns run_id in format: YYYYMMDD_HHMMSS_<uuid8>

    This is used for maintaining compatibility with existing code
    that expects the timestamp-based run_id format.

    Returns:
        Legacy format run_id string
    """
    from uuid import uuid4

    now = datetime.now()
    return now.strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8]
