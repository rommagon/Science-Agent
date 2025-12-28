#!/usr/bin/env python3
"""Display run history from the SQLite database.

Shows the last N runs with metadata and visualizes new publications per run.

Usage:
    python -m tools.db_run_history
    python -m tools.db_run_history --limit 20
"""

import argparse
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.sqlite_store import get_run_history, DEFAULT_DB_PATH


def format_run_history(runs: list[dict]) -> str:
    """Format run history for display.

    Args:
        runs: List of run dictionaries

    Returns:
        Formatted string for display
    """
    if not runs:
        return "No runs found in database."

    lines = []
    lines.append("=" * 90)
    lines.append("Run History (Last {} Runs)".format(len(runs)))
    lines.append("=" * 90)
    lines.append("")

    # Table header
    header = f"{'Run ID':<25} {'Started':<20} {'New':>6} {'Unchg':>6} {'Total':>6} {'Summ':>6}"
    lines.append(header)
    lines.append("-" * 90)

    # Table rows
    for run in runs:
        run_id_short = run["run_id"][:24]
        started = run["started_at"][:19].replace("T", " ")  # Strip to datetime
        new_count = run["new_count"]
        unchanged_count = run["unchanged_count"]
        total_deduped = run["total_deduped"]
        summarized = run["summarized_count"]

        row = f"{run_id_short:<25} {started:<20} {new_count:>6} {unchanged_count:>6} {total_deduped:>6} {summarized:>6}"
        lines.append(row)

    lines.append("")
    lines.append("=" * 90)
    lines.append("")

    # Visualization: New publications per run (simple ASCII bar chart)
    lines.append("New Publications Per Run:")
    lines.append("")

    # Calculate max for scaling
    max_new = max(run["new_count"] for run in runs) if runs else 1
    bar_width = 50

    for run in runs:
        run_id_short = run["run_id"][:16]
        new_count = run["new_count"]

        # Scale bar
        if max_new > 0:
            bar_length = int((new_count / max_new) * bar_width)
        else:
            bar_length = 0

        bar = "█" * bar_length
        lines.append(f"  {run_id_short:<16}  {new_count:>4}  {bar}")

    lines.append("")
    lines.append("=" * 90)
    lines.append(f"Legend: New=New publications, Unchg=Unchanged, Total=After dedup, Summ=Summarized")
    lines.append("=" * 90)

    return "\n".join(lines)


def main():
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Display run history from the database",
        epilog="Example: python -m tools.db_run_history --limit 20"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of recent runs to display (default: 10)"
    )
    parser.add_argument(
        "--db",
        type=str,
        default=DEFAULT_DB_PATH,
        help=f"Path to database file (default: {DEFAULT_DB_PATH})"
    )

    args = parser.parse_args()

    # Check if database exists
    if not Path(args.db).exists():
        print(f"Error: Database not found: {args.db}", file=sys.stderr)
        print("\nThe database has not been created yet.", file=sys.stderr)
        print("Run the pipeline at least once to create the database:", file=sys.stderr)
        print("  python run.py --since-days 7 --max-items-per-source 5", file=sys.stderr)
        sys.exit(1)

    # Get run history
    runs = get_run_history(limit=args.limit, db_path=args.db)

    if not runs:
        print("No run history found in database.", file=sys.stderr)
        print("\nThe database exists but contains no run history.", file=sys.stderr)
        print("Run the pipeline to generate run history:", file=sys.stderr)
        print("  python run.py --since-days 7 --max-items-per-source 5", file=sys.stderr)
        sys.exit(1)

    # Format and display
    output = format_run_history(runs)
    print(output)


if __name__ == "__main__":
    main()
