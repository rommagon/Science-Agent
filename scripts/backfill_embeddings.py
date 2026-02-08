#!/usr/bin/env python3
"""Backfill embeddings for existing publications.

This script generates embeddings for publications in the database that don't
have embeddings yet for the specified model.

Usage:
    python scripts/backfill_embeddings.py [--since-days N] [--limit N] [--dry-run]

Examples:
    # Backfill all publications missing embeddings
    python scripts/backfill_embeddings.py

    # Backfill only publications from the last 30 days
    python scripts/backfill_embeddings.py --since-days 30

    # Preview changes without updating database
    python scripts/backfill_embeddings.py --dry-run --limit 100

    # Use a specific model
    python scripts/backfill_embeddings.py --model text-embedding-3-small
"""

import argparse
import logging
import os
import sys
import time
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acitrack.semantic_search import (
    build_embedding_text,
    compute_content_hash,
    embed_text,
    embedding_to_bytes,
    get_embedding_dimension,
    get_openai_api_key,
    DEFAULT_EMBEDDING_MODEL,
)
from storage.store import get_store, get_database_url

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def backfill_embeddings(
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    since_days: Optional[int] = None,
    limit: Optional[int] = None,
    max_per_minute: int = 200,
    dry_run: bool = False,
) -> dict:
    """Backfill embeddings for publications missing them.

    Args:
        embedding_model: Name of the embedding model to use
        since_days: Only process publications from the last N days
        limit: Maximum number of publications to process
        max_per_minute: Maximum embeddings to generate per minute
        dry_run: If True, don't update the database

    Returns:
        Dictionary with backfill statistics
    """
    # Check for API key
    api_key = get_openai_api_key()
    if not api_key:
        logger.error("OpenAI API key not configured (SPOTITEARLY_LLM_API_KEY or OPENAI_API_KEY)")
        return {
            "success": False,
            "error": "OpenAI API key not configured",
            "processed": 0,
            "embedded": 0,
            "skipped": 0,
            "errors": 0,
        }

    store = get_store()
    database_url = get_database_url()

    # Get publications missing embeddings
    logger.info("Fetching publications missing embeddings for model: %s", embedding_model)

    if database_url:
        publications = store.get_publications_missing_embeddings(
            embedding_model=embedding_model,
            since_days=since_days,
            limit=limit,
            database_url=database_url,
        )
    else:
        publications = store.get_publications_missing_embeddings(
            embedding_model=embedding_model,
            since_days=since_days,
            limit=limit,
        )

    total = len(publications)
    logger.info("Found %d publications missing embeddings", total)

    if total == 0:
        return {
            "success": True,
            "processed": 0,
            "embedded": 0,
            "skipped": 0,
            "errors": 0,
        }

    embedded = 0
    skipped = 0
    errors = 0
    min_delay = 60.0 / max_per_minute
    embedding_dim = get_embedding_dimension(embedding_model)

    for i, pub in enumerate(publications):
        try:
            # Build embedding text
            text = build_embedding_text(pub)

            if not text or len(text.strip()) < 10:
                logger.debug("Skipping publication %s: insufficient text", pub["id"][:16])
                skipped += 1
                continue

            # Compute content hash
            content_hash = compute_content_hash(text)

            if dry_run:
                logger.debug(
                    "[DRY-RUN] Would embed publication %s (text_len=%d, hash=%s)",
                    pub["id"][:16],
                    len(text),
                    content_hash[:16],
                )
                embedded += 1
            else:
                # Generate embedding
                embedding = embed_text(text, model=embedding_model, api_key=api_key)

                if embedding is None:
                    logger.warning("Failed to generate embedding for %s", pub["id"][:16])
                    errors += 1
                    continue

                # Store embedding
                embedding_bytes = embedding_to_bytes(embedding)

                if database_url:
                    result = store.store_publication_embedding(
                        publication_id=pub["id"],
                        embedding_model=embedding_model,
                        embedding_dim=embedding_dim,
                        embedding=embedding_bytes,
                        content_hash=content_hash,
                        database_url=database_url,
                    )
                else:
                    result = store.store_publication_embedding(
                        publication_id=pub["id"],
                        embedding_model=embedding_model,
                        embedding_dim=embedding_dim,
                        embedding=embedding_bytes,
                        content_hash=content_hash,
                    )

                if result.get("success"):
                    embedded += 1
                else:
                    logger.warning(
                        "Failed to store embedding for %s: %s",
                        pub["id"][:16],
                        result.get("error"),
                    )
                    errors += 1

            # Progress logging
            if (i + 1) % 50 == 0:
                logger.info(
                    "Progress: %d/%d processed (%d embedded, %d skipped, %d errors)",
                    i + 1,
                    total,
                    embedded,
                    skipped,
                    errors,
                )

            # Rate limiting (skip in dry-run mode)
            if not dry_run and i < total - 1:
                time.sleep(min_delay)

        except Exception as e:
            errors += 1
            logger.error("Error processing publication %s: %s", pub.get("id", "?")[:16], e)

    return {
        "success": True,
        "processed": total,
        "embedded": embedded,
        "skipped": skipped,
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Backfill embeddings for existing publications",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_EMBEDDING_MODEL,
        help=f"Embedding model to use (default: {DEFAULT_EMBEDDING_MODEL})",
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
        "--max-per-minute",
        type=int,
        default=200,
        help="Maximum embeddings to generate per minute (default: 200)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without generating embeddings or updating database",
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

    # Check for API key
    api_key = get_openai_api_key()
    if not api_key and not args.dry_run:
        print("\n" + "=" * 70)
        print("ERROR: OpenAI API key not configured")
        print("=" * 70)
        print("Set one of the following environment variables:")
        print("  - SPOTITEARLY_LLM_API_KEY")
        print("  - OPENAI_API_KEY")
        print("=" * 70 + "\n")
        sys.exit(1)

    # Print execution plan
    print("\n" + "=" * 70)
    print("Embedding Backfill")
    print("=" * 70)
    print(f"Model:         {args.model}")
    print(f"Dimension:     {get_embedding_dimension(args.model)}")
    if args.since_days:
        print(f"Since days:    {args.since_days}")
    if args.limit:
        print(f"Limit:         {args.limit}")
    print(f"Max/minute:    {args.max_per_minute}")
    print(f"Dry run:       {args.dry_run}")
    print(f"API key:       {'[CONFIGURED]' if api_key else '[MISSING]'}")
    print("=" * 70 + "\n")

    # Run backfill
    result = backfill_embeddings(
        embedding_model=args.model,
        since_days=args.since_days,
        limit=args.limit,
        max_per_minute=args.max_per_minute,
        dry_run=args.dry_run,
    )

    # Print summary
    print("\n" + "=" * 70)
    print("BACKFILL COMPLETE")
    print("=" * 70)
    print(f"Processed:     {result.get('processed', 0)}")
    print(f"Embedded:      {result.get('embedded', 0)}")
    print(f"Skipped:       {result.get('skipped', 0)}")
    print(f"Errors:        {result.get('errors', 0)}")

    if args.dry_run:
        print("\n[DRY-RUN] No embeddings were generated or stored.")

    if result.get("error"):
        print(f"\nError: {result['error']}")

    print("=" * 70 + "\n")

    # Exit with error code if there were errors
    if result.get("errors", 0) > 0 or not result.get("success", True):
        sys.exit(1)


if __name__ == "__main__":
    main()
