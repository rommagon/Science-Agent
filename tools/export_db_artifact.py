#!/usr/bin/env python3
"""Export database artifact as gzipped SQLite file.

This script creates a compressed copy of the acitrack database for Drive upload.

Usage:
    python3 tools/export_db_artifact.py [--db-path PATH] [--output PATH]
"""

import argparse
import gzip
import logging
import shutil
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def export_db_artifact(
    db_path: Path = Path("data/db/acitrack.db"),
    output_path: Path = Path("data/output/latest_db.sqlite.gz")
) -> dict:
    """Export database as gzipped artifact.

    Args:
        db_path: Path to SQLite database
        output_path: Path to write gzipped database

    Returns:
        Dict with export status
    """
    logger.info("Exporting database: %s -> %s", db_path, output_path)

    # Check if database exists
    if not db_path.exists():
        logger.warning("Database not found: %s", db_path)
        return {
            "success": False,
            "error": f"Database not found: {db_path}"
        }

    # Create output directory
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Gzip the database
    try:
        with open(db_path, 'rb') as f_in:
            with gzip.open(output_path, 'wb', compresslevel=9) as f_out:
                shutil.copyfileobj(f_in, f_out)

        # Get file sizes
        original_size = db_path.stat().st_size
        compressed_size = output_path.stat().st_size
        compression_ratio = (1 - compressed_size / original_size) * 100

        logger.info("Database exported successfully")
        logger.info("Original size: %.2f MB", original_size / 1024 / 1024)
        logger.info("Compressed size: %.2f MB (%.1f%% reduction)",
                   compressed_size / 1024 / 1024, compression_ratio)

        return {
            "success": True,
            "output_path": str(output_path),
            "original_size_mb": original_size / 1024 / 1024,
            "compressed_size_mb": compressed_size / 1024 / 1024,
            "compression_ratio": compression_ratio
        }

    except Exception as e:
        logger.error("Failed to export database: %s", e)
        return {
            "success": False,
            "error": str(e)
        }


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Export database as gzipped artifact for Drive upload"
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("data/db/acitrack.db"),
        help="SQLite database path (default: data/db/acitrack.db)"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/output/latest_db.sqlite.gz"),
        help="Output gzipped file (default: data/output/latest_db.sqlite.gz)"
    )

    args = parser.parse_args()

    result = export_db_artifact(
        db_path=args.db_path,
        output_path=args.output
    )

    if result["success"]:
        print(f"\n✅ Export successful!")
        print(f"   Output: {result['output_path']}")
        print(f"   Original: {result['original_size_mb']:.2f} MB")
        print(f"   Compressed: {result['compressed_size_mb']:.2f} MB")
        print(f"   Compression: {result['compression_ratio']:.1f}%")
        sys.exit(0)
    else:
        print(f"\n❌ Export failed: {result.get('error', 'Unknown error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
