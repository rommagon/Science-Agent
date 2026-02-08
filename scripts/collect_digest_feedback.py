#!/usr/bin/env python3
"""Minimal HTTP receiver for weekly digest thumbs feedback links.

Usage:
  export DIGEST_FEEDBACK_SECRET="replace-with-long-random-secret"
  python scripts/collect_digest_feedback.py --port 8787
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Dict
from urllib.parse import parse_qs, urlparse

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from digest.data_access import get_database_url, log_publication_feedback
from digest.feedback import verify_feedback_signature

logger = logging.getLogger(__name__)


def _single(qs: Dict[str, list[str]], key: str) -> str | None:
    values = qs.get(key)
    if not values:
        return None
    return values[0]


def _response_html(message: str) -> bytes:
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Feedback Received</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f8f9fa; margin: 0; }}
    .box {{ max-width: 560px; margin: 48px auto; background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 28px; }}
    .title {{ font-size: 22px; font-weight: 700; color: #111827; margin: 0 0 10px 0; }}
    .body {{ color: #4b5563; line-height: 1.6; }}
  </style>
</head>
<body>
  <div class="box">
    <h1 class="title">Thanks for your feedback</h1>
    <div class="body">{message}</div>
  </div>
</body>
</html>
"""
    return html.encode("utf-8")


def make_handler(secret: str, max_age_seconds: int, db_path: str | None, database_url: str | None):
    class FeedbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path.rstrip("/") != "/feedback":
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")
                return

            qs = parse_qs(parsed.query, keep_blank_values=False)
            publication_id = _single(qs, "p")
            week_start = _single(qs, "w")
            week_end = _single(qs, "e")
            vote = _single(qs, "v")
            ts = _single(qs, "t")
            signature = _single(qs, "s")

            if not all([publication_id, week_start, week_end, vote, ts, signature]):
                self._write_html(400, "The feedback link is incomplete.")
                return

            if vote not in {"up", "down"}:
                self._write_html(400, "The feedback value is invalid.")
                return

            try:
                ts_int = int(ts)
            except ValueError:
                self._write_html(400, "The feedback link timestamp is invalid.")
                return

            if abs(int(time.time()) - ts_int) > max_age_seconds:
                self._write_html(410, "This feedback link has expired.")
                return

            signed_params = {
                "p": publication_id,
                "w": week_start,
                "e": week_end,
                "v": vote,
                "t": ts,
            }
            if not verify_feedback_signature(signed_params, signature, secret):
                self._write_html(403, "The feedback signature is invalid.")
                return

            ok = log_publication_feedback(
                week_start=week_start,
                week_end=week_end,
                publication_id=publication_id,
                vote=vote,
                source_ip=self.client_address[0] if self.client_address else None,
                user_agent=self.headers.get("User-Agent"),
                context={"timestamp": ts_int},
                db_path=db_path,
                database_url=database_url,
            )

            if not ok:
                self._write_html(500, "Feedback could not be saved. Please try again.")
                return

            vote_word = "Thumbs Up" if vote == "up" else "Thumbs Down"
            self._write_html(200, f"Your {vote_word} vote has been recorded.")

        def log_message(self, fmt: str, *args):  # noqa: A003
            logger.info("%s - - %s", self.address_string(), fmt % args)

        def _write_html(self, code: int, message: str):
            body = _response_html(message)
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return FeedbackHandler


def main():
    parser = argparse.ArgumentParser(description="Run weekly digest feedback receiver")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8787, help="Bind port (default: 8787)")
    parser.add_argument(
        "--max-age-seconds",
        type=int,
        default=60 * 60 * 24 * 90,
        help="Signed link max age in seconds (default: 90 days)",
    )
    parser.add_argument(
        "--db",
        type=str,
        help="Database URL or SQLite path (overrides DATABASE_URL env var)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    secret = os.environ.get("DIGEST_FEEDBACK_SECRET")
    if not secret:
        raise SystemExit("DIGEST_FEEDBACK_SECRET is required.")

    database_url = None
    db_path = None
    if args.db:
        if args.db.startswith("postgresql://"):
            database_url = args.db
        else:
            db_path = args.db
    else:
        database_url = get_database_url()
        if not database_url:
            default_db = Path(__file__).resolve().parents[1] / "data" / "db" / "acitrack.db"
            db_path = str(default_db)

    handler = make_handler(
        secret=secret,
        max_age_seconds=args.max_age_seconds,
        db_path=db_path,
        database_url=database_url,
    )
    server = HTTPServer((args.host, args.port), handler)
    logger.info("Feedback receiver listening on http://%s:%d/feedback", args.host, args.port)
    logger.info(
        "Database backend: %s",
        "PostgreSQL" if database_url else f"SQLite ({db_path})",
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
