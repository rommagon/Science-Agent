"""Configuration for daily and weekly pipeline runs."""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Tuple


RunType = Literal["daily", "weekly"]


@dataclass
class RunContext:
    """Context for a pipeline run (daily or weekly).

    Attributes:
        run_id: Identifier in format:
                - daily: daily-YYYY-MM-DD (e.g. daily-2026-01-10)
                - weekly: weekly-YYYY-WW (e.g. weekly-2026-02 for ISO week 2)
        run_type: Type of run ("daily" or "weekly")
        window_start: Start of lookback window (UTC)
        window_end: End of lookback window (UTC)
        lookback_hours: Number of hours in the lookback window (for daily)
        lookback_days: Number of days in the lookback window (for weekly)
    """
    run_id: str
    run_type: RunType
    window_start: datetime
    window_end: datetime
    lookback_hours: int = 0
    lookback_days: int = 0


# Pipeline configuration
RUN_FREQUENCY = os.getenv("ACITRACK_RUN_FREQUENCY", "daily")
LOOKBACK_HOURS = int(os.getenv("ACITRACK_LOOKBACK_HOURS", "48"))

# Feature flags
WRITE_SHEETS = os.getenv("ACITRACK_WRITE_SHEETS", "false").lower() in ("true", "1", "yes")


def compute_run_context(
    run_type: RunType = "daily",
    lookback_hours: int = None,
    lookback_days: int = None,
) -> RunContext:
    """Compute the run context for a pipeline execution (daily or weekly).

    Generates a typed run_id and calculates the time window for
    ingesting publications. Uses a rolling lookback window to avoid
    missing delayed postings.

    Args:
        run_type: Type of run ("daily" or "weekly")
        lookback_hours: Number of hours to look back (for daily runs).
                       Defaults to LOOKBACK_HOURS env var or 48.
        lookback_days: Number of days to look back (for weekly runs).
                      Defaults to 7.

    Returns:
        RunContext with run_id, run_type, and time window boundaries (UTC)

    Examples:
        >>> # Daily run
        >>> ctx = compute_run_context(run_type="daily", lookback_hours=48)
        >>> ctx.run_id
        'daily-2026-01-10'
        >>> ctx.run_type
        'daily'

        >>> # Weekly run
        >>> ctx = compute_run_context(run_type="weekly", lookback_days=7)
        >>> ctx.run_id
        'weekly-2026-02'
        >>> ctx.run_type
        'weekly'
    """
    # Get current time in UTC
    now_utc = datetime.now(timezone.utc)

    if run_type == "daily":
        # Daily run: use date-based ID with "daily-" prefix
        if lookback_hours is None:
            lookback_hours = LOOKBACK_HOURS

        run_id = f"daily-{now_utc.strftime('%Y-%m-%d')}"
        window_end = now_utc
        window_start = now_utc - timedelta(hours=lookback_hours)

        return RunContext(
            run_id=run_id,
            run_type="daily",
            window_start=window_start,
            window_end=window_end,
            lookback_hours=lookback_hours,
            lookback_days=0,
        )

    elif run_type == "weekly":
        # Weekly run: use ISO week-based ID with "weekly-" prefix
        if lookback_days is None:
            lookback_days = 7

        # Get ISO week number
        iso_year, iso_week, _ = now_utc.isocalendar()
        run_id = f"weekly-{iso_year}-{iso_week:02d}"

        window_end = now_utc
        window_start = now_utc - timedelta(days=lookback_days)

        return RunContext(
            run_id=run_id,
            run_type="weekly",
            window_start=window_start,
            window_end=window_end,
            lookback_hours=0,
            lookback_days=lookback_days,
        )

    else:
        raise ValueError(f"Invalid run_type: {run_type}. Must be 'daily' or 'weekly'.")


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
