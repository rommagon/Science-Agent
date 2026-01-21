#!/usr/bin/env python3
"""Export must-reads to JSON and Markdown for Drive upload.

This script generates must-reads artifacts for Google Drive:
- data/output/latest_must_reads.json (structured data)
- data/output/latest_must_reads.md (human-readable markdown)

Usage:
    python3 tools/export_must_reads.py [--since-days DAYS] [--limit N] [--no-ai]
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_server.must_reads import get_must_reads_from_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def format_markdown(must_reads_data: dict) -> str:
    """Format must-reads data as markdown.

    Args:
        must_reads_data: Output from get_must_reads_from_db

    Returns:
        Markdown string
    """
    md_lines = []

    # Header
    md_lines.append("# Must Reads - Latest Export")
    md_lines.append("")
    md_lines.append(f"**Generated:** {must_reads_data['generated_at']}")
    md_lines.append(f"**Window:** {must_reads_data['window_days']} days")
    md_lines.append(f"**Total Candidates:** {must_reads_data['total_candidates']}")
    md_lines.append(f"**Used AI:** {must_reads_data['used_ai']}")
    if must_reads_data['rerank_version']:
        md_lines.append(f"**Rerank Version:** {must_reads_data['rerank_version']}")
    md_lines.append("")
    md_lines.append("---")
    md_lines.append("")

    # Publications
    for i, mr in enumerate(must_reads_data['must_reads'], 1):
        md_lines.append(f"## {i}. {mr['title']}")
        md_lines.append("")
        md_lines.append(f"**Source:** {mr['source']} ({mr['venue']})")
        md_lines.append(f"**Date:** {mr['published_date']}")
        md_lines.append(f"**Score:** {mr['score_total']:.1f} "
                       f"(heuristic: {mr['score_components']['heuristic']:.1f}, "
                       f"llm: {mr['score_components'].get('llm', 'N/A')})")

        # Tags and confidence
        tags = mr.get('tags', [])
        if tags:
            md_lines.append(f"**Tags:** {', '.join(tags)}")

        confidence = mr.get('confidence')
        if confidence:
            md_lines.append(f"**Confidence:** {confidence}")

        md_lines.append("")
        md_lines.append(f"**Why it matters:** {mr['why_it_matters']}")
        md_lines.append("")

        # Key findings
        if mr['key_findings']:
            md_lines.append("**Key findings:**")
            for finding in mr['key_findings']:
                md_lines.append(f"- {finding}")
            md_lines.append("")

        # Link
        md_lines.append(f"**Link:** [{mr['url']}]({mr['url']})")
        md_lines.append("")
        md_lines.append("---")
        md_lines.append("")

    return "\n".join(md_lines)


def export_must_reads(
    since_days: int = 30,
    limit: int = 20,
    use_ai: bool = True,
    output_dir: Path = Path("data/output"),
    json_filename: str = "latest_must_reads.json",
    md_filename: str = "latest_must_reads.md",
    run_id: Optional[str] = None,
) -> dict:
    """Export must-reads to JSON and Markdown.

    Args:
        since_days: Number of days to look back
        limit: Maximum number of results
        use_ai: Whether to use AI reranking (requires OPENAI_API_KEY)
        output_dir: Output directory for files
        json_filename: Filename for JSON output (default: latest_must_reads.json)
        md_filename: Filename for Markdown output (default: latest_must_reads.md)
        run_id: Optional run identifier to load cached relevancy scores

    Returns:
        Dict with export status and paths
    """
    logger.info("Generating must-reads (since_days=%d, limit=%d, use_ai=%s, run_id=%s)",
                since_days, limit, use_ai, run_id)

    # Generate must-reads
    try:
        must_reads_data = get_must_reads_from_db(
            since_days=since_days,
            limit=limit,
            use_ai=use_ai,
            rerank_max_candidates=25,
            run_id=run_id,
        )
    except Exception as e:
        logger.error("Failed to generate must-reads: %s", e)
        raise

    logger.info("Generated %d must-reads (used_ai=%s)",
                len(must_reads_data['must_reads']),
                must_reads_data['used_ai'])

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write JSON
    json_path = output_dir / json_filename
    with open(json_path, "w") as f:
        json.dump(must_reads_data, f, indent=2)
    logger.info("Wrote JSON to: %s", json_path)

    # Write Markdown
    md_content = format_markdown(must_reads_data)
    md_path = output_dir / md_filename
    with open(md_path, "w") as f:
        f.write(md_content)
    logger.info("Wrote Markdown to: %s", md_path)

    return {
        "success": True,
        "json_path": str(json_path),
        "md_path": str(md_path),
        "count": len(must_reads_data['must_reads']),
        "used_ai": must_reads_data['used_ai']
    }


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Export must-reads to JSON and Markdown for Drive upload"
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=30,
        help="Number of days to look back (default: 30)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of results (default: 20)"
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Disable AI reranking (use heuristic only)"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/output"),
        help="Output directory (default: data/output)"
    )

    args = parser.parse_args()

    try:
        result = export_must_reads(
            since_days=args.since_days,
            limit=args.limit,
            use_ai=not args.no_ai,
            output_dir=args.output_dir
        )

        print(f"\n✅ Export successful!")
        print(f"   JSON: {result['json_path']}")
        print(f"   Markdown: {result['md_path']}")
        print(f"   Count: {result['count']}")
        print(f"   Used AI: {result['used_ai']}")

        sys.exit(0)

    except Exception as e:
        logger.error("Export failed: %s", e)
        print(f"\n❌ Export failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
