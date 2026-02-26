#!/usr/bin/env python3
"""Generate weekly digest email for SpotItEarly.

This script generates a weekly email containing the top publications
in cancer early detection research.

Usage:
    # Demo mode (renders + writes artifacts only)
    python scripts/generate_weekly_digest.py --week-start 2026-01-19 --demo

    # Send mode (actually sends email via SendGrid)
    python scripts/generate_weekly_digest.py --week-start 2026-01-19 --send --to me@example.com

    # Using week shortcuts
    python scripts/generate_weekly_digest.py --week this --demo
    python scripts/generate_weekly_digest.py --week last --demo
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jinja2 import Environment, FileSystemLoader

from digest.data_access import (
    get_publications_for_week,
    log_digest_send,
    get_database_url,
)
from digest.feedback import build_feedback_url
from digest.senders import get_sender, validate_sendgrid_config, validate_gmail_config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Try to use zoneinfo (Python 3.9+) or pytz as fallback
try:
    from zoneinfo import ZoneInfo
    NYC_TZ = ZoneInfo("America/New_York")
except ImportError:
    try:
        import pytz
        NYC_TZ = pytz.timezone("America/New_York")
    except ImportError:
        NYC_TZ = None
        logger.warning("No timezone library available, using UTC")


def get_week_start_nyc(reference_date: Optional[date] = None) -> date:
    """Get the start of the week (Monday) in America/New_York timezone.

    Args:
        reference_date: Date to use as reference (defaults to today)

    Returns:
        Date of Monday of that week
    """
    if reference_date is None:
        if NYC_TZ:
            now = datetime.now(NYC_TZ)
            reference_date = now.date()
        else:
            reference_date = date.today()

    # Monday is weekday 0
    days_since_monday = reference_date.weekday()
    return reference_date - timedelta(days=days_since_monday)


def parse_week_shortcut(shortcut: str) -> date:
    """Parse week shortcut like 'this' or 'last'.

    Args:
        shortcut: 'this' for current week, 'last' for previous week

    Returns:
        Week start date (Monday)
    """
    this_week_start = get_week_start_nyc()

    if shortcut.lower() == "this":
        return this_week_start
    elif shortcut.lower() == "last":
        return this_week_start - timedelta(days=7)
    else:
        raise ValueError(f"Unknown week shortcut: {shortcut}. Use 'this' or 'last'.")


def render_digest(
    data: dict,
    templates_dir: Optional[str] = None,
) -> tuple:
    """Render digest templates.

    Args:
        data: Digest data including must_reads, honorable_mentions, etc.
        templates_dir: Path to templates directory

    Returns:
        Tuple of (html_content, text_content)
    """
    if templates_dir is None:
        templates_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates"
        )

    env = Environment(
        loader=FileSystemLoader(templates_dir),
        autoescape=True,
    )

    html_template = env.get_template("weekly_digest.html.j2")
    text_template = env.get_template("weekly_digest.txt.j2")

    html_content = html_template.render(**data)
    text_content = text_template.render(**data)

    return html_content, text_content


def write_artifacts(
    output_dir: str,
    html_content: str,
    text_content: str,
    data: dict,
) -> dict:
    """Write digest artifacts to disk.

    Args:
        output_dir: Directory to write artifacts
        html_content: Rendered HTML content
        text_content: Rendered plain text content
        data: Digest data for JSON export

    Returns:
        Dictionary with artifact paths
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    paths = {}

    # Write HTML
    html_path = output_path / "digest.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    paths["html"] = str(html_path)

    # Write text
    text_path = output_path / "digest.txt"
    with open(text_path, "w", encoding="utf-8") as f:
        f.write(text_content)
    paths["text"] = str(text_path)

    # Write JSON (for debugging/auditing)
    json_path = output_path / "digest.json"

    # Prepare JSON-serializable data
    json_data = {
        "week_start": data["week_start"],
        "week_end": data["week_end"],
        "generated_at": datetime.now().isoformat(),
        "total_candidates": data["total_candidates"],
        "scoring_method": data["scoring_method"],
        "must_reads": [],
        "honorable_mentions": [],
    }

    # Convert must_reads to JSON-safe format
    for item in data["must_reads"]:
        json_item = {}
        for key, value in item.items():
            if isinstance(value, (datetime, date)):
                json_item[key] = value.isoformat()
            elif value is None or isinstance(value, (str, int, float, bool, list, dict)):
                json_item[key] = value
            else:
                json_item[key] = str(value)
        json_data["must_reads"].append(json_item)

    for item in data.get("honorable_mentions", []):
        json_item = {}
        for key, value in item.items():
            if isinstance(value, (datetime, date)):
                json_item[key] = value.isoformat()
            elif value is None or isinstance(value, (str, int, float, bool, list, dict)):
                json_item[key] = value
            else:
                json_item[key] = str(value)
        json_data["honorable_mentions"].append(json_item)

    # Include debug ranking data if available
    if "debug_ranking" in data:
        json_data["debug_ranking"] = data["debug_ranking"]

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    paths["json"] = str(json_path)

    return paths


def generate_subject(
    week_start: date,
    week_end: date,
    custom_subject: Optional[str] = None,
) -> str:
    """Generate email subject line.

    Args:
        week_start: Start of week
        week_end: End of week
        custom_subject: Custom subject line (optional)

    Returns:
        Email subject string
    """
    if custom_subject:
        return custom_subject

    start_str = week_start.strftime("%b %d")
    end_str = week_end.strftime("%b %d")

    return f"SpotItEarly Must-Reads — Week of {start_str}–{end_str}"


def main():
    parser = argparse.ArgumentParser(
        description="Generate weekly digest email for SpotItEarly",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Demo mode (renders + writes artifacts)
  python scripts/generate_weekly_digest.py --week-start 2026-01-19 --demo

  # Send to test recipients
  python scripts/generate_weekly_digest.py --week-start 2026-01-19 --send --to me@example.com

  # Using week shortcuts
  python scripts/generate_weekly_digest.py --week this --demo
  python scripts/generate_weekly_digest.py --week last --demo

  # Preview in browser
  python scripts/generate_weekly_digest.py --week this --demo --preview
        """,
    )

    # Date range arguments
    parser.add_argument(
        "--week-start",
        type=str,
        help="Start of week (YYYY-MM-DD). Defaults to start of current week in NYC timezone.",
    )
    parser.add_argument(
        "--week-end",
        type=str,
        help="End of week (YYYY-MM-DD). Defaults to week-start + 6 days.",
    )
    parser.add_argument(
        "--week",
        type=str,
        choices=["this", "last"],
        help="Week shortcut: 'this' for current week, 'last' for previous week.",
    )

    # Selection arguments
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="Number of top publications to include (default: 5)",
    )
    parser.add_argument(
        "--honorable-mentions",
        type=int,
        default=0,
        help="Number of honorable mentions to include (default: 0)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=70,
        help="Minimum relevancy score threshold. Only publications scoring at or "
             "above this value are included in the digest (default: 70)",
    )

    # Mode arguments
    parser.add_argument(
        "--demo",
        action="store_true",
        default=True,
        help="Demo mode: renders + writes artifacts only (default: true)",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        default=False,
        help="Actually send email (requires --to and either --gmail or SendGrid config)",
    )
    parser.add_argument(
        "--gmail",
        action="store_true",
        default=False,
        help="Use Gmail SMTP instead of SendGrid (requires GMAIL_ADDRESS and GMAIL_APP_PASSWORD)",
    )

    # Email arguments
    parser.add_argument(
        "--to",
        type=str,
        help="Comma-separated list of recipient emails (required if --send)",
    )
    parser.add_argument(
        "--subject",
        type=str,
        help="Custom email subject line",
    )
    parser.add_argument(
        "--feedback-base-url",
        type=str,
        help=(
            "Public feedback endpoint URL (e.g. https://feedback.example.com/feedback). "
            "If omitted, DIGEST_FEEDBACK_BASE_URL env var is used."
        ),
    )

    # Database arguments
    parser.add_argument(
        "--db",
        type=str,
        help="Database URL or path (overrides DATABASE_URL env var)",
    )

    # Output arguments
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Output directory for artifacts (default: data/outputs/weekly-digest/<start>_<end>/)",
    )

    # Utility arguments
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Open digest.html in default browser after generating",
    )
    parser.add_argument(
        "--debug-ranking",
        action="store_true",
        help="Show ranking diagnostics: top 20 candidates with full score breakdown",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Validate send mode requirements
    if args.send:
        if not args.to:
            print("ERROR: --to is required when using --send")
            sys.exit(1)

        if args.gmail:
            config = validate_gmail_config()
            if not config["valid"]:
                print("ERROR: Gmail not configured:")
                for error in config["errors"]:
                    print(f"  - {error}")
                print("\nTo set up Gmail:")
                print("  1. Enable 2FA on your Google account")
                print("  2. Go to https://myaccount.google.com/apppasswords")
                print("  3. Generate an app password for 'Mail'")
                print("  4. Set environment variables:")
                print("     export GMAIL_ADDRESS='your.email@gmail.com'")
                print("     export GMAIL_APP_PASSWORD='your-16-char-app-password'")
                sys.exit(1)
        else:
            config = validate_sendgrid_config()
            if not config["valid"]:
                print("ERROR: SendGrid not configured:")
                for error in config["errors"]:
                    print(f"  - {error}")
                print("\nAlternatively, use --gmail flag for Gmail SMTP")
                sys.exit(1)

    # Determine week start/end
    if args.week:
        week_start = parse_week_shortcut(args.week)
    elif args.week_start:
        week_start = date.fromisoformat(args.week_start)
    else:
        week_start = get_week_start_nyc()

    if args.week_end:
        week_end = date.fromisoformat(args.week_end)
    else:
        week_end = week_start + timedelta(days=6)

    # Determine output directory
    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "outputs", "weekly-digest",
            f"{week_start.isoformat()}_{week_end.isoformat()}"
        )

    # Configure database
    if args.db:
        if args.db.startswith("postgresql://"):
            os.environ["DATABASE_URL"] = args.db
            database_url = args.db
            db_path = None
        else:
            database_url = None
            db_path = args.db
    else:
        database_url = get_database_url()
        db_path = None

    # Print execution plan
    print("\n" + "=" * 70)
    print("WEEKLY DIGEST GENERATION")
    print("=" * 70)
    print(f"Week Start:    {week_start.isoformat()}")
    print(f"Week End:      {week_end.isoformat()}")
    print(f"Top N:         {args.top_n}")
    print(f"Min Score:     {args.min_score}")
    print(f"Honorable:     {args.honorable_mentions}")
    print(f"Mode:          {'send' if args.send else 'demo'}")
    print(f"Output Dir:    {output_dir}")
    print(f"Database:      {'PostgreSQL' if database_url else 'SQLite'}")
    if args.send and args.to:
        print(f"Recipients:    {args.to}")
    print("=" * 70 + "\n")

    feedback_base_url = args.feedback_base_url or os.environ.get("DIGEST_FEEDBACK_BASE_URL")
    feedback_secret = os.environ.get("DIGEST_FEEDBACK_SECRET")
    feedback_enabled = bool(feedback_base_url and feedback_secret)
    if feedback_base_url and not feedback_secret:
        logger.warning(
            "DIGEST_FEEDBACK_BASE_URL is set but DIGEST_FEEDBACK_SECRET is missing. "
            "Per-paper feedback links will be disabled."
        )

    # Fetch publications
    logger.info("Fetching publications for week %s to %s...", week_start, week_end)

    data = get_publications_for_week(
        week_start=week_start,
        week_end=week_end,
        top_n=args.top_n,
        honorable_mentions=args.honorable_mentions,
        db_path=db_path,
        database_url=database_url,
        debug_ranking=args.debug_ranking,
        min_relevancy_score=args.min_score,
    )

    # Add signed thumbs up/down URLs per publication when feedback is configured.
    if feedback_enabled:
        for item in data.get("must_reads", []):
            publication_id = item.get("id")
            if not publication_id:
                continue
            item["thumbs_up_url"] = build_feedback_url(
                base_url=feedback_base_url,
                publication_id=publication_id,
                week_start=week_start.isoformat(),
                week_end=week_end.isoformat(),
                vote="up",
                secret=feedback_secret,
            )
            item["thumbs_down_url"] = build_feedback_url(
                base_url=feedback_base_url,
                publication_id=publication_id,
                week_start=week_start.isoformat(),
                week_end=week_end.isoformat(),
                vote="down",
                secret=feedback_secret,
            )
    data["feedback_enabled"] = feedback_enabled

    print(f"Total candidates: {data['total_candidates']}")
    print(f"Must reads selected: {len(data['must_reads'])}")
    print(f"Honorable mentions: {len(data['honorable_mentions'])}")
    print(f"Scoring method: {data['scoring_method']}")

    # Show debug ranking if requested
    if args.debug_ranking and "debug_ranking" in data:
        debug = data["debug_ranking"]
        print("\n" + "-" * 70)
        print("RANKING DIAGNOSTICS")
        print("-" * 70)
        print("Ranked by relevancy_score only")
        print(f"Total candidates: {debug.get('total_candidates', 0)}")
        print(f"Total with relevancy_score: {debug.get('total_with_relevancy', 0)}")
        distribution = debug.get("relevancy_distribution", {})
        print(f"High (80+): {distribution.get('high_80_plus', 0)}")
        print(f"Moderate (65-79): {distribution.get('moderate_65_79', 0)}")
        print(f"Exploratory (<65): {distribution.get('exploratory_below_65', 0)}")

        if debug.get("ranking_warnings"):
            print("\n⚠️  RANKING WARNINGS:")
            for warning in debug["ranking_warnings"]:
                print(f"   {warning}")

        print("\nTop 20 candidates (Ranked by relevancy_score only):")
        print("-" * 70)
        for candidate in debug.get("top_20_candidates", []):
            marker = "★" if candidate["rank"] <= args.top_n else " "
            score = candidate["relevancy_score"]
            score_display = f"{score:5.1f}" if score is not None else "  N/A"
            credibility = candidate["credibility_score"]
            credibility_display = f"{credibility:5.1f}" if credibility is not None else "  N/A"
            pub_date = candidate["publication_date"] or "N/A"
            print(
                f"  {marker} #{candidate['rank']:2d} "
                f"[rel={score_display} cred={credibility_display} date={pub_date}] "
                f"{candidate['title']}"
            )
            if args.verbose:
                print(
                    f"       rel={candidate['relevancy_score'] if candidate['relevancy_score'] is not None else 'N/A'} "
                    f"cred={candidate['credibility_score'] if candidate['credibility_score'] is not None else 'N/A'} "
                    f"date={candidate['publication_date']}"
                )
        print("-" * 70)

    if not data["must_reads"]:
        print("\nWARNING: No publications found for this week!")
        print("The digest will be empty.")

    # Render templates
    logger.info("Rendering templates...")

    html_content, text_content = render_digest(data)

    # Write artifacts
    logger.info("Writing artifacts to %s...", output_dir)

    artifact_paths = write_artifacts(
        output_dir=output_dir,
        html_content=html_content,
        text_content=text_content,
        data=data,
    )

    print("\nArtifacts written:")
    for name, path in artifact_paths.items():
        print(f"  - {name}: {path}")

    # Send or demo
    subject = generate_subject(week_start, week_end, args.subject)
    recipients = args.to.split(",") if args.to else []

    if args.send:
        send_mode = "gmail" if args.gmail else "sendgrid"
        logger.info("Sending email via %s...", send_mode.upper())

        sender = get_sender(send_mode=send_mode)
        result = sender.send(
            to=recipients,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
        )

        if result["success"]:
            print(f"\nEmail sent successfully to: {', '.join(recipients)}")
            send_status = "success"
            error = None
        else:
            print(f"\nEmail send failed: {result['message']}")
            send_status = "failed"
            error = result["message"]

    else:
        # Demo mode
        sender = get_sender(send_mode="demo")
        result = sender.send(
            to=recipients or ["demo@example.com"],
            subject=subject,
            html_content=html_content,
            text_content=text_content,
        )
        send_status = "demo"
        error = None

    # Log to database
    logger.info("Logging digest send to database...")

    log_digest_send(
        week_start=week_start,
        week_end=week_end,
        top_n=args.top_n,
        honorable_mentions=args.honorable_mentions,
        recipients=recipients,
        selected_ids=[item["id"] for item in data["must_reads"]],
        output_dir=output_dir,
        send_mode="send" if args.send else "demo",
        send_status=send_status,
        error=error,
        db_path=db_path,
        database_url=database_url,
    )

    # Preview in browser
    if args.preview:
        html_path = artifact_paths.get("html")
        if html_path:
            print(f"\nOpening in browser: {html_path}")
            webbrowser.open(f"file://{os.path.abspath(html_path)}")

    # Print summary
    print("\n" + "=" * 70)
    print("DIGEST GENERATION COMPLETE")
    print("=" * 70)

    if data["must_reads"]:
        print("\nTop publications selected:")
        for i, item in enumerate(data["must_reads"], 1):
            title = item["title"][:60] + "..." if len(item["title"]) > 60 else item["title"]
            score = item.get("relevancy_score")
            score_display = f"{score:.1f}" if score is not None else "N/A"
            print(f"  {i}. [{score_display}] {title}")

    print("\n" + "=" * 70 + "\n")


if __name__ == "__main__":
    main()
