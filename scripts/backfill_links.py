#!/usr/bin/env python3
"""Backfill canonical URLs for existing publications.

This script scans publications in the database that are missing canonical URLs
and attempts to resolve them using available identifiers (DOI, PMID, URL patterns).

Usage:
    python scripts/backfill_links.py [--since-days N] [--limit N] [--dry-run]

Examples:
    # Backfill all publications missing canonical URLs
    python scripts/backfill_links.py

    # Backfill only publications from the last 30 days
    python scripts/backfill_links.py --since-days 30

    # Preview changes without updating database
    python scripts/backfill_links.py --dry-run --limit 100
"""

import argparse
import logging
import os
import sys
from collections import defaultdict

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from enrich.canonical_url import resolve_canonical_url
from storage.store import get_store, get_database_url

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def backfill_canonical_urls(
    since_days: int = None,
    limit: int = None,
    dry_run: bool = False,
) -> dict:
    """Backfill canonical URLs for publications missing them.

    Args:
        since_days: Only process publications from the last N days
        limit: Maximum number of publications to process
        dry_run: If True, don't update the database

    Returns:
        Dictionary with backfill statistics
    """
    store = get_store()
    database_url = get_database_url()

    # Get publications missing canonical URLs
    logger.info("Fetching publications missing canonical URLs...")

    if database_url:
        publications = store.get_publications_missing_canonical_url(
            since_days=since_days,
            limit=limit,
            database_url=database_url,
        )
    else:
        publications = store.get_publications_missing_canonical_url(
            since_days=since_days,
            limit=limit,
        )

    total_scanned = len(publications)
    logger.info("Found %d publications missing canonical URLs", total_scanned)

    if total_scanned == 0:
        return {
            "scanned": 0,
            "updated": 0,
            "unresolved": 0,
            "errors": 0,
            "unresolved_examples": [],
            "stats_by_source_type": {},
        }

    updated = 0
    unresolved = 0
    errors = 0
    unresolved_examples = []
    stats_by_source_type = defaultdict(lambda: {"total": 0, "resolved": 0})

    for i, pub in enumerate(publications):
        try:
            # Resolve canonical URL
            canonical_url, doi, pmid, source_type = resolve_canonical_url(pub)

            # Track stats by source type
            st = source_type or "unknown"
            stats_by_source_type[st]["total"] += 1

            if canonical_url:
                stats_by_source_type[st]["resolved"] += 1

                if not dry_run:
                    # Update database
                    if database_url:
                        result = store.update_publication_canonical_url(
                            publication_id=pub["id"],
                            canonical_url=canonical_url,
                            doi=doi,
                            pmid=pmid,
                            source_type=source_type,
                            database_url=database_url,
                        )
                    else:
                        result = store.update_publication_canonical_url(
                            publication_id=pub["id"],
                            canonical_url=canonical_url,
                            doi=doi,
                            pmid=pmid,
                            source_type=source_type,
                        )

                    if result.get("success") and result.get("updated"):
                        updated += 1
                    elif not result.get("success"):
                        errors += 1
                        logger.warning(
                            "Failed to update publication %s: %s",
                            pub["id"][:16],
                            result.get("error"),
                        )
                else:
                    updated += 1
                    logger.debug(
                        "[DRY-RUN] Would update %s: canonical_url=%s, doi=%s, pmid=%s",
                        pub["id"][:16],
                        canonical_url,
                        doi,
                        pmid,
                    )
            else:
                unresolved += 1
                if len(unresolved_examples) < 10:
                    unresolved_examples.append({
                        "id": pub["id"][:16],
                        "title": (pub.get("title") or "")[:60],
                        "url": (pub.get("url") or "")[:80],
                        "source": pub.get("source") or "",
                    })

            # Progress logging
            if (i + 1) % 100 == 0:
                logger.info(
                    "Progress: %d/%d processed (%d updated, %d unresolved)",
                    i + 1,
                    total_scanned,
                    updated,
                    unresolved,
                )

        except Exception as e:
            errors += 1
            logger.error("Error processing publication %s: %s", pub.get("id", "?")[:16], e)

    # Convert defaultdict to regular dict
    stats_by_source_type = dict(stats_by_source_type)

    return {
        "scanned": total_scanned,
        "updated": updated,
        "unresolved": unresolved,
        "errors": errors,
        "unresolved_examples": unresolved_examples,
        "stats_by_source_type": stats_by_source_type,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Backfill canonical URLs for existing publications",
    )
    parser.add_argument(
        "--since-days",
        type=int,
        help="Only process publications from the last N days",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of publications to process",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without updating database",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Print execution plan
    print("\n" + "=" * 70)
    print("Canonical URL Backfill")
    print("=" * 70)
    if args.since_days:
        print(f"Since days:    {args.since_days}")
    if args.limit:
        print(f"Limit:         {args.limit}")
    print(f"Dry run:       {args.dry_run}")
    print("=" * 70 + "\n")

    # Run backfill
    result = backfill_canonical_urls(
        since_days=args.since_days,
        limit=args.limit,
        dry_run=args.dry_run,
    )

    # Print summary
    print("\n" + "=" * 70)
    print("BACKFILL COMPLETE")
    print("=" * 70)
    print(f"Scanned:       {result['scanned']}")
    print(f"Updated:       {result['updated']}")
    print(f"Unresolved:    {result['unresolved']}")
    print(f"Errors:        {result['errors']}")

    if result["stats_by_source_type"]:
        print("\nBy source type:")
        for source_type, stats in sorted(result["stats_by_source_type"].items()):
            pct = (stats["resolved"] / stats["total"] * 100) if stats["total"] > 0 else 0
            print(f"  {source_type:12s}: {stats['resolved']:4d}/{stats['total']:4d} resolved ({pct:.1f}%)")

    if result["unresolved_examples"]:
        print("\nExample unresolved publications:")
        for ex in result["unresolved_examples"][:5]:
            print(f"  - {ex['id']}: {ex['title']}...")
            if ex["url"]:
                print(f"    URL: {ex['url']}")
            print(f"    Source: {ex['source']}")

    if args.dry_run:
        print("\n[DRY-RUN] No changes were made to the database.")

    print("=" * 70 + "\n")

    # Exit with error code if there were errors
    if result["errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
