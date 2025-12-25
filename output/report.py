"""Generate output reports from publications."""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_report(
    outdir: str,
    run_id: str,
) -> None:
    """Generate and save a Markdown report from changes.

    Args:
        outdir: Output directory for reports
        run_id: Unique identifier for this run
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
            for pub in new_pubs:
                title = pub.get("title", "Untitled")
                url = pub.get("url", "")
                source = pub.get("source", "Unknown")
                date = pub.get("date", "Unknown date")

                # Format as Markdown link if URL exists
                if url:
                    f.write(f"- **[{title}]({url})**\n")
                else:
                    f.write(f"- **{title}**\n")

                f.write(f"  - Source: {source}\n")
                f.write(f"  - Date: {date}\n\n")

        # Unchanged publications section
        unchanged_count = count_total - count_new
        f.write(f"## 📁 Unchanged ({unchanged_count})\n\n")

        if unchanged_count == 0:
            f.write("No unchanged publications.\n\n")
        else:
            for pub in unchanged_pubs:
                title = pub.get("title", "Untitled")
                source = pub.get("source", "Unknown")
                date = pub.get("date", "Unknown date")
                f.write(f"- {title} – {source} – {date}\n")

    logger.info("Markdown report saved to %s", report_path)
    print(f"Report saved: {report_path}")
