#!/usr/bin/env python3
"""Check for suspicious future dates in raw publications JSON files.

This script scans the latest raw publications JSON file and reports any
publications with dates more than 2 days in the future.

Usage:
    python -m tools.check_future_dates
    python -m tools.check_future_dates --file data/raw/RUNID_publications.json
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path


def check_future_dates(json_path: str) -> dict:
    """Check for future dates in a publications JSON file.

    Args:
        json_path: Path to publications JSON file

    Returns:
        Dictionary with check results
    """
    try:
        with open(json_path, "r") as f:
            publications = json.load(f)
    except FileNotFoundError:
        return {
            "error": f"File not found: {json_path}",
            "future_dates": []
        }
    except json.JSONDecodeError as e:
        return {
            "error": f"Invalid JSON: {e}",
            "future_dates": []
        }

    # Calculate future threshold: now + 2 days
    now = datetime.now()
    future_threshold = now + timedelta(days=2)

    future_dates = []

    for pub in publications:
        date_str = pub.get("date", "")
        if not date_str:
            continue

        try:
            # Parse ISO format date
            pub_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            # Convert to naive if needed
            if pub_date.tzinfo is not None:
                pub_date = pub_date.replace(tzinfo=None)

            if pub_date > future_threshold:
                future_dates.append({
                    "id": pub.get("id", ""),
                    "title": pub.get("title", ""),
                    "source": pub.get("source", ""),
                    "date": date_str,
                    "date_raw": pub.get("date_raw", "N/A"),
                    "date_source": pub.get("date_source", "N/A"),
                })

        except Exception as e:
            # Skip unparseable dates
            continue

    return {
        "total_publications": len(publications),
        "future_threshold": future_threshold.strftime("%Y-%m-%d"),
        "future_dates_count": len(future_dates),
        "future_dates": future_dates
    }


def find_latest_publications_json() -> str:
    """Find the most recent publications JSON file.

    Returns:
        Path to latest file, or None if not found
    """
    raw_dir = Path("data/raw")
    if not raw_dir.exists():
        return None

    json_files = list(raw_dir.glob("*_publications.json"))
    if not json_files:
        return None

    # Sort by modification time, most recent first
    json_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(json_files[0])


def main():
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Check for suspicious future dates in publications JSON",
        epilog="Example: python -m tools.check_future_dates"
    )
    parser.add_argument(
        "--file",
        type=str,
        help="Path to publications JSON file (default: latest in data/raw/)"
    )

    args = parser.parse_args()

    # Determine which file to check
    if args.file:
        json_path = args.file
    else:
        json_path = find_latest_publications_json()
        if not json_path:
            print("Error: No publications JSON files found in data/raw/", file=sys.stderr)
            print("Run the pipeline first: python run.py --since-days 7 --max-items-per-source 5",
                  file=sys.stderr)
            sys.exit(1)

    print(f"Checking: {json_path}")
    print("=" * 70)

    results = check_future_dates(json_path)

    if "error" in results:
        print(f"Error: {results['error']}", file=sys.stderr)
        sys.exit(1)

    print(f"Total publications: {results['total_publications']}")
    print(f"Future threshold: {results['future_threshold']}")
    print(f"Publications with future dates: {results['future_dates_count']}")
    print("=" * 70)

    if results['future_dates']:
        print("\nFuture dates found:")
        print()
        for item in results['future_dates']:
            print(f"ID: {item['id'][:16]}...")
            print(f"  Title: {item['title'][:70]}...")
            print(f"  Source: {item['source']}")
            print(f"  Date: {item['date']}")
            print(f"  Date Raw: {item['date_raw'][:80]}")
            print(f"  Date Source: {item['date_source']}")
            print()
        print("=" * 70)
        print(f"\nWARNING: {results['future_dates_count']} publication(s) have suspicious future dates!")
        sys.exit(1)
    else:
        print("\n✓ No suspicious future dates found!")
        sys.exit(0)


if __name__ == "__main__":
    main()
