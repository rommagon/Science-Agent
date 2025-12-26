"""Generate output reports from publications."""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_report(
    outdir: str,
    run_id: str,
    max_items_per_source: int = None,
) -> None:
    """Generate and save a Markdown report from changes.

    Args:
        outdir: Output directory for reports
        run_id: Unique identifier for this run
        max_items_per_source: Maximum items to include per source (None = no limit)
    """
    logger.info("Generating Markdown report for run %s", run_id)

    # Load changes JSON
    changes_path = Path(outdir) / "raw" / f"{run_id}_changes.json"
    if not changes_path.exists():
        logger.error("Changes file not found: %s", changes_path)
        return

    try:
        with open(changes_path, "r") as f:
            changes_data = json.load(f)
    except Exception as e:
        logger.error("Failed to load changes file: %s", e)
        return

    # Extract data
    timestamp = changes_data.get("timestamp", datetime.now().isoformat())
    count_new = changes_data.get("count_new", 0)
    count_total = changes_data.get("count_total", 0)
    publications = changes_data.get("publications", [])

    # Separate by status and collect source names
    new_pubs = [pub for pub in publications if pub.get("status") == "NEW"]
    unchanged_pubs = [pub for pub in publications if pub.get("status") == "UNCHANGED"]
    sources = sorted(set(pub.get("source", "Unknown") for pub in publications))

    # Sort new publications by date descending (most recent first)
    new_pubs.sort(key=lambda p: p.get("date", ""), reverse=True)

    # Sort unchanged publications by date descending
    unchanged_pubs.sort(key=lambda p: p.get("date", ""), reverse=True)

    # Apply max_items_per_source limit if specified
    # Track overflow counts for display
    new_overflow = {}
    unchanged_overflow = {}

    if max_items_per_source:
        # Count total items per source for NEW
        new_source_totals = {}
        for pub in new_pubs:
            source = pub.get("source", "Unknown")
            new_source_totals[source] = new_source_totals.get(source, 0) + 1

        # Limit new publications per source
        limited_new_pubs = []
        source_counts = {}
        for pub in new_pubs:
            source = pub.get("source", "Unknown")
            count = source_counts.get(source, 0)
            if count < max_items_per_source:
                limited_new_pubs.append(pub)
                source_counts[source] = count + 1

        # Calculate overflow for each source
        for source, total in new_source_totals.items():
            if total > max_items_per_source:
                new_overflow[source] = total - max_items_per_source

        new_pubs = limited_new_pubs

        # Count total items per source for UNCHANGED
        unchanged_source_totals = {}
        for pub in unchanged_pubs:
            source = pub.get("source", "Unknown")
            unchanged_source_totals[source] = unchanged_source_totals.get(source, 0) + 1

        # Limit unchanged publications per source
        limited_unchanged_pubs = []
        source_counts = {}
        for pub in unchanged_pubs:
            source = pub.get("source", "Unknown")
            count = source_counts.get(source, 0)
            if count < max_items_per_source:
                limited_unchanged_pubs.append(pub)
                source_counts[source] = count + 1

        # Calculate overflow for each source
        for source, total in unchanged_source_totals.items():
            if total > max_items_per_source:
                unchanged_overflow[source] = total - max_items_per_source

        unchanged_pubs = limited_unchanged_pubs

        total_new_hidden = sum(new_overflow.values())
        total_unchanged_hidden = sum(unchanged_overflow.values())
        logger.info(
            "Applied max_items_per_source=%d limit to report (hidden: %d new, %d unchanged)",
            max_items_per_source,
            total_new_hidden,
            total_unchanged_hidden,
        )

    # Create output directory
    output_dir = Path(outdir) / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate Markdown report
    report_path = output_dir / f"{run_id}_report.md"

    with open(report_path, "w") as f:
        # Header
        f.write("# AciTrack – Weekly Research Changes\n\n")
        f.write(f"**Run ID:** {run_id}  \n")
        f.write(f"**Generated:** {timestamp}  \n")
        f.write(f"**Sources:** {', '.join(sources)}\n\n")

        # New publications section
        f.write(f"## 🆕 New This Run ({count_new})\n\n")

        if count_new == 0:
            f.write("No new publications detected in this run.\n\n")
        else:
            # Group publications by source to track when to show overflow
            current_source = None
            source_item_count = 0

            for pub in new_pubs:
                title = pub.get("title", "Untitled")
                url = pub.get("url", "")
                source = pub.get("source", "Unknown")
                date = pub.get("date", "Unknown date")
                one_liner = pub.get("one_liner", "")
                essence_bullets = pub.get("essence_bullets", [])

                # If we switched sources and there was overflow, show it
                if current_source and source != current_source:
                    if current_source in new_overflow:
                        overflow_count = new_overflow[current_source]
                        f.write(f"  *+ {overflow_count} more NEW items from {current_source} not shown (stored in changes JSON)*\n\n")
                    source_item_count = 0

                current_source = source
                source_item_count += 1

                # Format as Markdown link if URL exists
                if url:
                    f.write(f"- **[{title}]({url})**\n")
                else:
                    f.write(f"- **{title}**\n")

                f.write(f"  - Source: {source}\n")
                f.write(f"  - Date: {date}\n")

                # Add one-liner if available
                if one_liner:
                    f.write(f"  - *{one_liner}*\n")

                # Add essence bullets if available
                if essence_bullets:
                    f.write("\n")
                    for bullet in essence_bullets:
                        f.write(f"  - {bullet}\n")

                f.write("\n")

            # Show overflow for the last source if needed
            if current_source and current_source in new_overflow:
                overflow_count = new_overflow[current_source]
                f.write(f"  *+ {overflow_count} more NEW items from {current_source} not shown (stored in changes JSON)*\n\n")

        # Unchanged publications section
        unchanged_count = count_total - count_new
        f.write(f"## 📁 Unchanged ({unchanged_count})\n\n")

        if unchanged_count == 0:
            f.write("No unchanged publications.\n\n")
        else:
            # Group publications by source to track when to show overflow
            current_source = None
            source_item_count = 0

            for pub in unchanged_pubs:
                title = pub.get("title", "Untitled")
                source = pub.get("source", "Unknown")
                date = pub.get("date", "Unknown date")

                # If we switched sources and there was overflow, show it
                if current_source and source != current_source:
                    if current_source in unchanged_overflow:
                        overflow_count = unchanged_overflow[current_source]
                        f.write(f"  *+ {overflow_count} more UNCHANGED items from {current_source} not shown (stored in changes JSON)*\n\n")
                    source_item_count = 0

                current_source = source
                source_item_count += 1

                f.write(f"- {title} – {source} – {date}\n")

            # Show overflow for the last source if needed
            if current_source and current_source in unchanged_overflow:
                overflow_count = unchanged_overflow[current_source]
                f.write(f"\n  *+ {overflow_count} more UNCHANGED items from {current_source} not shown (stored in changes JSON)*\n")

    logger.info("Markdown report saved to %s", report_path)
    print(f"Report saved: {report_path}")
