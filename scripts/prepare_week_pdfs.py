#!/usr/bin/env python3
"""Wednesday orchestrator: fetch OA PDFs for the coming week's must-reads.

Runs each Wednesday ahead of the Thursday digest. For each of the top-N
must-reads:

1. Look up (DOI, PMID) from the publications table.
2. Try the OA cascade (enrich/oa_pdf.py): Unpaywall → Europe PMC →
   Crossref → bioRxiv.
3. On hit: save the PDF to <pdf-dir>/<pub_id>-<sha12>.pdf and upsert a
   row in pdf_store.
4. On miss (or non-attachable license): upsert a pending_fetch row with
   status='pending'.

Finally, email the founder a summary of every pending item (with Emory
proxy + upload links) so they can upload PDFs manually before Thursday.

Idempotent: re-running for the same week won't re-download PDFs that
already landed, and pending_fetch uses (publication_id, week_start) as
its composite PK.

Usage:
    # Dry run for the current week (no DB writes, no email)
    python scripts/prepare_week_pdfs.py --week this --dry-run

    # Real run for the current week
    python scripts/prepare_week_pdfs.py --week this \\
        --pdf-dir /home/ubuntu/sie-ai/pdfs \\
        --upload-base-url https://ai.spotitearly.com \\
        --alert-to founder@spotitearly.com \\
        --gmail
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

# Allow imports from repo root when invoked as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from digest.alerts import send_fetch_alert
from digest.data_access import get_database_url, get_publications_for_week
from digest.senders import get_sender, validate_gmail_config
from enrich.oa_pdf import OaPdfResult, fetch_oa_pdf
from storage.pdf_tracking import (
    get_connection,
    get_pdf_record,
    list_pending_fetch,
    upsert_pdf_record,
    upsert_pending_fetch,
)

# Mirror generate_weekly_digest.py's week-boundary logic so Wednesday prep
# targets the same Monday–Sunday window as Thursday's digest send.
try:
    from zoneinfo import ZoneInfo
    _NYC_TZ = ZoneInfo("America/New_York")
except ImportError:  # pragma: no cover
    try:
        import pytz
        _NYC_TZ = pytz.timezone("America/New_York")
    except ImportError:
        _NYC_TZ = None


def get_week_start_nyc(reference_date: Optional[date] = None) -> date:
    from datetime import datetime
    if reference_date is None:
        reference_date = datetime.now(_NYC_TZ).date() if _NYC_TZ else date.today()
    return reference_date - timedelta(days=reference_date.weekday())


def parse_week_shortcut(shortcut: str) -> date:
    this_week = get_week_start_nyc()
    if shortcut.lower() == "this":
        return this_week
    if shortcut.lower() == "last":
        return this_week - timedelta(days=7)
    raise ValueError(f"Unknown week shortcut: {shortcut}")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_PDF_DIR = "/home/ubuntu/sie-ai/pdfs"


def _sha12(sha256: str) -> str:
    return sha256[:12]


def _filename_for(pub_id: str, sha256: str) -> str:
    # pub_id may contain characters unsafe for filenames on some FSes.
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in pub_id)
    return f"{safe}-{_sha12(sha256)}.pdf"


def _write_pdf(pdf_dir: Path, pub_id: str, result: OaPdfResult) -> Path:
    """Write the PDF to disk; return the absolute path."""
    pdf_dir.mkdir(parents=True, exist_ok=True)
    path = pdf_dir / _filename_for(pub_id, result.sha256)
    # Only write if missing — sha-addressed content is immutable.
    if not path.exists():
        with open(path, "wb") as f:
            f.write(result.pdf_bytes)
    return path


def _collect_identifiers(item: dict) -> tuple[Optional[str], Optional[str]]:
    """Return (doi, pmid) from a must-read row, stripping whitespace."""
    doi = (item.get("doi") or "").strip() or None
    pmid = (item.get("pmid") or "").strip() or None
    return doi, pmid


def _original_url(item: dict) -> Optional[str]:
    """Best effort canonical URL for the paper — used in the alert email."""
    for key in ("canonical_url", "url"):
        val = item.get(key)
        if val:
            return val
    doi = item.get("doi")
    if doi:
        return f"https://doi.org/{doi}"
    pmid = item.get("pmid")
    if pmid:
        return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    return None


def prepare_week(
    week_start: date,
    week_end: date,
    pdf_dir: Path,
    upload_base_url: str,
    unpaywall_email: str,
    top_n: int = 5,
    min_score: float = 70.0,
    dry_run: bool = False,
    db_path: Optional[str] = None,
    database_url: Optional[str] = None,
) -> dict:
    """Run the Wednesday cascade for a single week.

    Returns a summary dict with 'fetched', 'pending', and 'skipped' lists.
    """
    logger.info(
        "Preparing PDFs for week %s – %s (top_n=%d, dry_run=%s)",
        week_start, week_end, top_n, dry_run,
    )

    data = get_publications_for_week(
        week_start=week_start,
        week_end=week_end,
        top_n=top_n,
        honorable_mentions=0,
        db_path=db_path,
        database_url=database_url,
        min_relevancy_score=min_score,
    )
    must_reads: List[dict] = data.get("must_reads", [])
    logger.info("Found %d must-reads for the week", len(must_reads))

    fetched: List[dict] = []
    pending: List[dict] = []
    skipped: List[dict] = []  # non-attachable license — still alertable

    if dry_run:
        conn = None
    else:
        conn = get_connection(database_url=database_url, sqlite_path=db_path)

    try:
        for item in must_reads:
            pub_id = item.get("id") or item.get("publication_id")
            if not pub_id:
                logger.warning("Skipping must-read without id: %s", item.get("title"))
                continue

            title = item.get("title", "(untitled)")
            doi, pmid = _collect_identifiers(item)

            # Fast path: we already have a PDF for this paper. Nothing to do.
            if conn is not None:
                existing = get_pdf_record(conn, pub_id)
                if existing:
                    logger.info(
                        "pdf_store already has %s (%s) — skipping fetch",
                        pub_id, existing.get("license"),
                    )
                    fetched.append({
                        "publication_id": pub_id,
                        "title": title,
                        "license": existing.get("license"),
                        "source_api": existing.get("source_api"),
                        "file_path": existing.get("file_path"),
                        "cached": True,
                    })
                    continue

            if not doi and not pmid:
                logger.info("No DOI/PMID for %s — queueing manual fetch", pub_id)
                pending.append({
                    "publication_id": pub_id,
                    "title": title,
                    "original_url": _original_url(item),
                    "doi": doi,
                    "venue": item.get("venue"),
                    "reason": "no-identifiers",
                })
                continue

            result = fetch_oa_pdf(doi=doi, pmid=pmid, email=unpaywall_email)
            if result is None:
                logger.info("OA miss for %s (doi=%s pmid=%s)", pub_id, doi, pmid)
                pending.append({
                    "publication_id": pub_id,
                    "title": title,
                    "original_url": _original_url(item),
                    "doi": doi,
                    "venue": item.get("venue"),
                    "reason": "oa-miss",
                })
                continue

            if not result.attachable:
                logger.info(
                    "OA hit for %s but license=%s is not attachable — queueing manual",
                    pub_id, result.license,
                )
                pending.append({
                    "publication_id": pub_id,
                    "title": title,
                    "original_url": _original_url(item),
                    "doi": doi,
                    "venue": item.get("venue"),
                    "reason": f"license:{result.license}",
                })
                skipped.append({"publication_id": pub_id, "license": result.license})
                continue

            # Attachable hit — persist PDF + DB record.
            if dry_run:
                logger.info(
                    "[dry-run] would save %s (%s, %d bytes, license=%s)",
                    pub_id, result.source_api, len(result.pdf_bytes), result.license,
                )
            else:
                path = _write_pdf(pdf_dir, pub_id, result)
                upsert_pdf_record(
                    conn,
                    publication_id=pub_id,
                    file_path=str(path),
                    sha256=result.sha256,
                    license=result.license,
                    source_api=result.source_api,
                    bytes_len=len(result.pdf_bytes),
                )
                logger.info(
                    "Saved %s → %s (%s, %s, %d bytes)",
                    pub_id, path, result.source_api, result.license, len(result.pdf_bytes),
                )
            fetched.append({
                "publication_id": pub_id,
                "title": title,
                "license": result.license,
                "source_api": result.source_api,
                "bytes_len": len(result.pdf_bytes),
            })

        # Persist pending rows so reminder cron + upload app can see them.
        if not dry_run and conn is not None and pending:
            for p in pending:
                upsert_pending_fetch(
                    conn,
                    publication_id=p["publication_id"],
                    week_start=week_start,
                    original_url=p.get("original_url"),
                    status="pending",
                )
    finally:
        if conn is not None:
            conn.close()

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "must_reads_count": len(must_reads),
        "fetched": fetched,
        "pending": pending,
        "skipped": skipped,
    }


def collect_pending_for_reminder(
    week_start: date,
    week_end: date,
    top_n: int,
    min_score: float,
    db_path: Optional[str],
    database_url: Optional[str],
) -> List[dict]:
    """Build the alert payload for the Thursday reminder.

    Query pending_fetch for rows still in 'pending' status for this week,
    then enrich each with title/venue/doi from the must-reads list so the
    email can render the same way as Wednesday's initial alert.
    """
    conn = get_connection(database_url=database_url, sqlite_path=db_path)
    try:
        pending_rows = list_pending_fetch(conn, week_start=week_start, status="pending")
    finally:
        conn.close()

    if not pending_rows:
        return []

    # Enrich from the must-reads list (which has titles/venues/doi).
    data = get_publications_for_week(
        week_start=week_start,
        week_end=week_end,
        top_n=top_n,
        honorable_mentions=0,
        db_path=db_path,
        database_url=database_url,
        min_relevancy_score=min_score,
    )
    by_id = {item.get("id") or item.get("publication_id"): item for item in data.get("must_reads", [])}

    out: List[dict] = []
    for row in pending_rows:
        pub_id = row["publication_id"]
        meta = by_id.get(pub_id, {})
        out.append({
            "publication_id": pub_id,
            "title": meta.get("title", "(title unavailable)"),
            "original_url": row.get("original_url") or _original_url(meta),
            "doi": meta.get("doi"),
            "venue": meta.get("venue"),
        })
    return out


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Wednesday OA-PDF prep orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--week-start", type=str, help="YYYY-MM-DD (Monday of target week)")
    p.add_argument("--week-end", type=str, help="YYYY-MM-DD (Sunday of target week)")
    p.add_argument("--week", choices=["this", "last"], help="Shortcut: 'this' or 'last'")

    p.add_argument("--top-n", type=int, default=5, help="Number of must-reads to prep (default 5)")
    p.add_argument("--min-score", type=float, default=70.0, help="Minimum relevancy score (default 70)")

    p.add_argument(
        "--pdf-dir",
        type=str,
        default=os.environ.get("PDF_STORE_DIR", DEFAULT_PDF_DIR),
        help=f"Directory to write PDFs into (default {DEFAULT_PDF_DIR} or $PDF_STORE_DIR)",
    )
    p.add_argument(
        "--upload-base-url",
        type=str,
        default=os.environ.get("UPLOAD_BASE_URL"),
        help="Base URL of the upload app (e.g. https://ai.spotitearly.com)",
    )
    p.add_argument(
        "--unpaywall-email",
        type=str,
        default=os.environ.get("UNPAYWALL_EMAIL") or os.environ.get("FROM_EMAIL"),
        help="Contact email for Unpaywall API (default $UNPAYWALL_EMAIL / $FROM_EMAIL)",
    )

    p.add_argument("--alert-to", type=str, help="Comma-separated alert recipients")
    p.add_argument("--gmail", action="store_true", help="Send alert via Gmail SMTP")
    p.add_argument("--demo-alert", action="store_true", help="Print alert email to stdout instead of sending")
    p.add_argument("--no-alert", action="store_true", help="Skip the alert email entirely")
    p.add_argument(
        "--reminder",
        action="store_true",
        help="Thursday reminder mode: skip the OA cascade, re-query pending_fetch, "
             "and send a reminder email if anything is still pending.",
    )

    p.add_argument("--db", type=str, help="Database URL or path (overrides DATABASE_URL)")
    p.add_argument("--dry-run", action="store_true", help="Log everything, write nothing, send nothing")
    p.add_argument("--verbose", "-v", action="store_true")

    return p.parse_args()


def main() -> int:
    args = _parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Resolve week boundaries
    if args.week:
        week_start = parse_week_shortcut(args.week)
    elif args.week_start:
        week_start = date.fromisoformat(args.week_start)
    else:
        week_start = get_week_start_nyc()
    week_end = date.fromisoformat(args.week_end) if args.week_end else week_start + timedelta(days=6)

    # DB config (same precedence as generate_weekly_digest.py)
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

    if not args.unpaywall_email:
        print("ERROR: --unpaywall-email (or UNPAYWALL_EMAIL/FROM_EMAIL env var) is required", file=sys.stderr)
        return 2

    if not args.upload_base_url and not args.dry_run and not args.no_alert:
        print("ERROR: --upload-base-url is required unless --dry-run or --no-alert", file=sys.stderr)
        return 2

    if args.reminder:
        pending = collect_pending_for_reminder(
            week_start=week_start,
            week_end=week_end,
            top_n=args.top_n,
            min_score=args.min_score,
            db_path=db_path,
            database_url=database_url,
        )
        print("=" * 70)
        print(f"Reminder mode — week {week_start} – {week_end}")
        print(f"Pending items: {len(pending)}")
        print("=" * 70)
        for p in pending:
            print(f"  ✗ {p['publication_id']} — {p.get('title', '')[:60]}")
        summary = {"pending": pending, "fetched": [], "skipped": []}
        is_reminder = True
    else:
        summary = prepare_week(
            week_start=week_start,
            week_end=week_end,
            pdf_dir=Path(args.pdf_dir),
            upload_base_url=args.upload_base_url or "",
            unpaywall_email=args.unpaywall_email,
            top_n=args.top_n,
            min_score=args.min_score,
            dry_run=args.dry_run,
            db_path=db_path,
            database_url=database_url,
        )
        is_reminder = False

        # Console summary
        print("=" * 70)
        print(f"Week:          {summary['week_start']} – {summary['week_end']}")
        print(f"Must-reads:    {summary['must_reads_count']}")
        print(f"Fetched:       {len(summary['fetched'])}")
        print(f"Pending:       {len(summary['pending'])}")
        if summary["skipped"]:
            print(f"Non-attachable: {len(summary['skipped'])}")
        print("=" * 70)
        for f in summary["fetched"]:
            marker = "(cached)" if f.get("cached") else f"({f.get('source_api')}, {f.get('license')})"
            print(f"  ✓ {f['publication_id']} {marker} — {f.get('title', '')[:60]}")
        for p in summary["pending"]:
            print(f"  ✗ {p['publication_id']} ({p.get('reason')}) — {p.get('title', '')[:60]}")

    # Alert email
    if args.no_alert:
        logger.info("--no-alert set: skipping alert send")
        return 0
    if args.dry_run:
        logger.info("--dry-run set: skipping alert send")
        return 0
    if not summary["pending"]:
        logger.info("No pending items — no alert needed")
        return 0

    if args.demo_alert:
        sender = get_sender(send_mode="demo")
    elif args.gmail:
        cfg = validate_gmail_config()
        if not cfg["valid"]:
            for e in cfg["errors"]:
                print(f"ERROR: {e}", file=sys.stderr)
            return 2
        sender = get_sender(send_mode="gmail")
    else:
        sender = get_sender(send_mode="sendgrid")

    recipients = [r.strip() for r in (args.alert_to or "").split(",") if r.strip()]
    if not recipients and not args.demo_alert:
        print("ERROR: --alert-to is required when sending", file=sys.stderr)
        return 2

    result = send_fetch_alert(
        sender=sender,
        recipients=recipients or ["demo@example.com"],
        pending_items=summary["pending"],
        upload_base_url=args.upload_base_url,
        is_reminder=is_reminder,
    )
    if not result.get("success"):
        print(f"ERROR: alert send failed: {result.get('message')}", file=sys.stderr)
        return 1

    print(f"Alert sent to {recipients}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
