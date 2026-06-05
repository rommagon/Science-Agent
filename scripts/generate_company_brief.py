#!/usr/bin/env python3
"""Generate the weekly SpotitEarly Company Brief email.

Consolidates four tools into one branded weekly email, in fixed order:
    1. Competitive landscape    (HTTP: BI_BRIEF_URL)
    2. Science Agent articles  (this repo, in-process)
    3. Grant Agent funding      (HTTP: GRANT_BRIEF_URL)
    4. Regulatory updates       (HTTP: REGULATORY_BRIEF_URL)

Mirrors ``scripts/generate_weekly_digest.py``: same demo/send modes, same
``digest.senders`` Gmail path, same artifact-writing pattern.

Usage:
    # Demo (render + write artifacts, no send) — opens in browser
    python scripts/generate_company_brief.py --week last --demo --preview

    # Send via Gmail SMTP to recipients
    python scripts/generate_company_brief.py --send --gmail \\
        --to research@spotitearly.com,rom@spotitearly.com

Environment (send mode / HTTP feeds):
    GMAIL_ADDRESS, GMAIL_APP_PASSWORD   — Gmail SMTP (reused from the digest)
    BRIEF_TOKEN                         — shared X-Brief-Token for the feeds
    GRANT_BRIEF_URL                     — e.g. http://127.0.0.1:8105/api/brief
    REGULATORY_BRIEF_URL               — e.g. http://127.0.0.1:8100/api/summaries/brief
    DATABASE_URL                        — Science publications DB (Postgres)
"""

import argparse
import json
import logging
import os
import sys
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from digest.company_brief.aggregate import BriefConfig, build_company_brief
from digest.company_brief.render import render_company_brief
from digest.senders import get_sender, validate_gmail_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo

    NYC_TZ = ZoneInfo("America/New_York")
except ImportError:  # pragma: no cover
    NYC_TZ = None


def _today_nyc() -> date:
    if NYC_TZ:
        return datetime.now(NYC_TZ).date()
    return date.today()


def _week_start_monday(reference: Optional[date] = None) -> date:
    ref = reference or _today_nyc()
    return ref - timedelta(days=ref.weekday())


def resolve_window(args) -> tuple[date, date]:
    """Resolve (week_start, week_end) from args.

    Precedence: explicit --week-start/--week-end > --week this/last (Monday
    week) > default trailing 7 days ending today (NYC).
    """
    if args.week:
        start = _week_start_monday()
        if args.week == "last":
            start = start - timedelta(days=7)
        return start, start + timedelta(days=6)

    if args.week_start:
        start = date.fromisoformat(args.week_start)
        end = date.fromisoformat(args.week_end) if args.week_end else start + timedelta(days=6)
        return start, end

    today = _today_nyc()
    return today - timedelta(days=7), today


def generate_subject(week_start: date, week_end: date, custom: Optional[str]) -> str:
    if custom:
        return custom
    return (
        f"SpotitEarly Company Brief — Week of "
        f"{week_start.strftime('%b %-d')}–{week_end.strftime('%b %-d')}"
    )


def write_artifacts(output_dir: str, html: str, text: str, sections: list) -> dict:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    (out / "company_brief.html").write_text(html, encoding="utf-8")
    paths["html"] = str(out / "company_brief.html")
    (out / "company_brief.txt").write_text(text, encoding="utf-8")
    paths["text"] = str(out / "company_brief.txt")
    (out / "company_brief.json").write_text(
        json.dumps({"generated_at": datetime.now().isoformat(), "sections": sections}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    paths["json"] = str(out / "company_brief.json")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the weekly SpotitEarly Company Brief email",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--week-start", type=str, help="Start date YYYY-MM-DD")
    parser.add_argument("--week-end", type=str, help="End date YYYY-MM-DD")
    parser.add_argument("--week", type=str, choices=["this", "last"], help="Monday-week shortcut")

    parser.add_argument("--demo", action="store_true", default=True, help="Render + write artifacts only (default)")
    parser.add_argument("--send", action="store_true", default=False, help="Actually send the email")
    parser.add_argument("--gmail", action="store_true", default=False, help="Use Gmail SMTP (required with --send)")
    parser.add_argument("--to", type=str, help="Comma-separated recipients (required with --send)")
    parser.add_argument("--subject", type=str, help="Custom subject line")

    parser.add_argument("--science-top-n", type=int, default=5)
    parser.add_argument("--science-min-score", type=float, default=70.0)
    parser.add_argument("--grant-must-apply", type=float, default=75.0)

    parser.add_argument("--output-dir", type=str, help="Artifact output directory")
    parser.add_argument("--preview", action="store_true", help="Open HTML in browser after generating")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.send:
        if not args.to:
            print("ERROR: --to is required when using --send")
            sys.exit(1)
        if not args.gmail:
            print("ERROR: --gmail is required when using --send (Gmail SMTP is the only sender)")
            sys.exit(1)
        cfg_check = validate_gmail_config()
        if not cfg_check["valid"]:
            print("ERROR: Gmail not configured:")
            for err in cfg_check["errors"]:
                print(f"  - {err}")
            sys.exit(1)

    week_start, week_end = resolve_window(args)

    output_dir = args.output_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "outputs", "company-brief",
        f"{week_start.isoformat()}_{week_end.isoformat()}",
    )

    cfg = BriefConfig.from_env(
        science_top_n=args.science_top_n,
        science_min_score=args.science_min_score,
        grant_must_apply=args.grant_must_apply,
    )

    print("\n" + "=" * 70)
    print("COMPANY BRIEF GENERATION")
    print("=" * 70)
    print(f"Window:      {week_start.isoformat()} .. {week_end.isoformat()}")
    print(f"Mode:        {'send' if args.send else 'demo'}")
    print(f"Grant feed:  {cfg.grant_brief_url or '(unset)'}")
    print(f"Reg feed:    {cfg.regulatory_brief_url or '(unset)'}")
    print(f"BI feed:     {cfg.bi_brief_url or '(unset)'}")
    print(f"Brief token: {'set' if cfg.brief_token else 'UNSET — HTTP feeds will be skipped'}")
    print("=" * 70 + "\n")

    sections = build_company_brief(week_start, week_end, cfg)
    html, text = render_company_brief(sections, week_start, week_end)

    paths = write_artifacts(output_dir, html, text, sections)
    print("Artifacts written:")
    for name, path in paths.items():
        print(f"  - {name}: {path}")

    counts = ", ".join(f"{s['section_title'].split(':')[0]}={len(s.get('items') or [])}" for s in sections)
    print(f"\nSection item counts: {counts}")

    subject = generate_subject(week_start, week_end, args.subject)
    recipients = [r.strip() for r in args.to.split(",")] if args.to else []

    if args.send:
        sender = get_sender(send_mode="gmail")
        result = sender.send(to=recipients, subject=subject, html_content=html, text_content=text)
        if result["success"]:
            print(f"\nEmail sent successfully to: {', '.join(recipients)}")
        else:
            print(f"\nEmail send FAILED: {result['message']}")
            sys.exit(1)
    else:
        get_sender(send_mode="demo").send(
            to=recipients or ["demo@example.com"],
            subject=subject,
            html_content=html,
            text_content=text,
        )

    if args.preview:
        print(f"\nOpening in browser: {paths['html']}")
        webbrowser.open(f"file://{os.path.abspath(paths['html'])}")

    print("\n" + "=" * 70)
    print("COMPANY BRIEF COMPLETE")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
