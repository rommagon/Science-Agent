"""Flask app for manually uploading OA-PDF-miss papers.

Designed to run behind Cloudflare Access + Cloudflare Tunnel. CF Access
is the sole source of truth for identity — the app trusts the
`Cf-Access-Authenticated-User-Email` header and additionally gates on a
short allow-list (ALLOWED_UPLOADER_EMAILS env var).

Routes:
    GET  /healthz             — liveness probe
    GET  /                    — redirects to /pending
    GET  /pending             — list this-week pending_fetch rows
    GET  /upload/<pub_id>     — upload form for one pending item
    POST /upload/<pub_id>     — handle file upload, save to disk, upsert

Storage layout matches scripts/prepare_week_pdfs.py:
    <PDF_STORE_DIR>/<pub_id>-<sha12>.pdf

Security rules:
    - Only real PDFs (magic-byte check) are accepted.
    - Max upload size enforced via MAX_CONTENT_LENGTH (default 50MB).
    - Publication-id is sanitized into a safe filename slug.
    - CF Access JWT verification is NOT re-done in app code — we trust
      CF Access to only forward authenticated requests, and double-check
      the email allow-list as a belt-and-braces safeguard.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

# Allow absolute imports when executed standalone (python -m upload_app).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import (  # noqa: E402
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from digest.data_access import get_database_url  # noqa: E402
from enrich.oa_pdf import ATTACHABLE_LICENSES  # noqa: E402
from storage.pdf_tracking import (  # noqa: E402
    get_connection,
    list_pending_fetch,
    mark_uploaded,
    upsert_pdf_record,
)

logger = logging.getLogger(__name__)

# ---- config defaults --------------------------------------------------

DEFAULT_PDF_DIR = "/home/ubuntu/sie-ai/pdfs"
DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB
MIN_PDF_BYTES = 1024  # anything below this is almost certainly junk
PDF_MAGIC = b"%PDF"
CF_ACCESS_EMAIL_HEADER = "Cf-Access-Authenticated-User-Email"

# License user can declare at upload time (gates whether the digest
# is allowed to attach this PDF vs only link to a proxy URL).
USER_LICENSES: List[str] = sorted(ATTACHABLE_LICENSES) + ["bronze", "unknown"]


# ---- helpers ----------------------------------------------------------

def _slug(pub_id: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in pub_id)


def _week_start_today() -> date:
    """Monday of the current week (UTC — good enough for the upload UI)."""
    today = datetime.utcnow().date()
    return today - timedelta(days=today.weekday())


def _is_real_pdf(head: bytes) -> bool:
    return head.startswith(PDF_MAGIC)


def _authed_email(app: Flask) -> Optional[str]:
    """Return the authenticated user email, or None if not allowed.

    Trusts the CF Access header. If ALLOW_INSECURE=1 is set (local dev),
    uses 'dev@local' as a stand-in. Always checks the allow-list.
    """
    email = request.headers.get(CF_ACCESS_EMAIL_HEADER)
    if not email and app.config.get("ALLOW_INSECURE"):
        email = "dev@local"
    if not email:
        return None

    allow_list = app.config.get("ALLOWED_UPLOADER_EMAILS") or []
    if allow_list and email.lower() not in [e.lower() for e in allow_list]:
        logger.warning("Upload denied for %s (not in allow-list)", email)
        return None
    return email


def _require_auth(app: Flask):
    """Abort 401/403 if the caller isn't authenticated. Stash email in g."""
    email = _authed_email(app)
    if not email:
        abort(401, description="Cloudflare Access authentication required")
    g.user_email = email


# ---- app factory ------------------------------------------------------

def create_app(config: Optional[dict] = None) -> Flask:
    """Build the Flask app. Accepts an optional config dict for tests."""
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
        static_folder=None,  # no static assets yet
    )

    # Defaults — callers (tests, gunicorn wrapper) may override.
    app.config.update(
        PDF_STORE_DIR=os.environ.get("PDF_STORE_DIR", DEFAULT_PDF_DIR),
        MAX_CONTENT_LENGTH=int(os.environ.get("MAX_UPLOAD_BYTES", DEFAULT_MAX_BYTES)),
        SECRET_KEY=os.environ.get("UPLOAD_APP_SECRET_KEY", os.urandom(32)),
        DATABASE_URL=os.environ.get("DATABASE_URL") or get_database_url(),
        SQLITE_PATH=os.environ.get("SQLITE_PATH"),
        ALLOW_INSECURE=os.environ.get("ALLOW_INSECURE") == "1",
        ALLOWED_UPLOADER_EMAILS=[
            e.strip() for e in os.environ.get("ALLOWED_UPLOADER_EMAILS", "").split(",")
            if e.strip()
        ],
    )
    if config:
        app.config.update(config)

    def _open_db():
        return get_connection(
            database_url=app.config.get("DATABASE_URL"),
            sqlite_path=app.config.get("SQLITE_PATH"),
        )

    # --------- routes --------------------------------------------------

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}, 200

    @app.get("/")
    def index():
        return redirect(url_for("pending_list"))

    @app.get("/pending")
    def pending_list():
        _require_auth(app)
        week_start = _week_start_today()
        conn = _open_db()
        try:
            rows = list_pending_fetch(conn, week_start=week_start, status="pending")
        finally:
            conn.close()

        return render_template(
            "pending.html",
            user=g.user_email,
            week_start=week_start.isoformat(),
            rows=rows,
        )

    @app.get("/upload/<pub_id>")
    def upload_form(pub_id: str):
        _require_auth(app)
        week_start = _week_start_today()
        conn = _open_db()
        try:
            rows = list_pending_fetch(conn, week_start=week_start)
        finally:
            conn.close()

        row = next((r for r in rows if r["publication_id"] == pub_id), None)
        if row is None:
            # Still allow upload even if there's no pending_fetch row —
            # an operator might be pre-loading a PDF. But warn in the UI.
            row = {
                "publication_id": pub_id,
                "week_start": week_start.isoformat(),
                "status": "unknown",
                "original_url": None,
            }
        return render_template(
            "upload.html",
            user=g.user_email,
            pub_id=pub_id,
            row=row,
            licenses=USER_LICENSES,
            default_license="cc-by",
        )

    @app.post("/upload/<pub_id>")
    def upload_handle(pub_id: str):
        _require_auth(app)

        file = request.files.get("pdf")
        if file is None or not file.filename:
            flash("Please choose a PDF file.", "error")
            return redirect(url_for("upload_form", pub_id=pub_id))

        license_declared = (request.form.get("license") or "unknown").strip()
        if license_declared not in USER_LICENSES:
            flash(f"Unknown license: {license_declared}", "error")
            return redirect(url_for("upload_form", pub_id=pub_id))

        data = file.read()
        if len(data) < MIN_PDF_BYTES:
            flash(f"File too small ({len(data)} bytes) — not a real PDF.", "error")
            return redirect(url_for("upload_form", pub_id=pub_id))
        if not _is_real_pdf(data[:8]):
            flash("File is not a PDF (bad magic bytes).", "error")
            return redirect(url_for("upload_form", pub_id=pub_id))

        sha256 = hashlib.sha256(data).hexdigest()
        pdf_dir = Path(app.config["PDF_STORE_DIR"])
        pdf_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = pdf_dir / f"{_slug(pub_id)}-{sha256[:12]}.pdf"

        # Atomic write: tmp file + rename
        tmp_path = pdf_path.with_suffix(".tmp")
        with open(tmp_path, "wb") as fh:
            fh.write(data)
        os.replace(tmp_path, pdf_path)

        week_start = _week_start_today()
        conn = _open_db()
        try:
            upsert_pdf_record(
                conn,
                publication_id=pub_id,
                file_path=str(pdf_path),
                sha256=sha256,
                license=license_declared,
                source_api="manual-upload",
                bytes_len=len(data),
            )
            mark_uploaded(conn, pub_id, week_start)
        finally:
            conn.close()

        logger.info(
            "Upload accepted: pub_id=%s sha=%s bytes=%d user=%s license=%s",
            pub_id, sha256[:12], len(data), g.user_email, license_declared,
        )
        flash(f"Uploaded {len(data)} bytes for {pub_id}.", "success")
        return redirect(url_for("pending_list"))

    # --------- error handlers -----------------------------------------

    @app.errorhandler(401)
    def _unauthorized(e):
        return render_template("error.html", code=401, message=str(e.description)), 401

    @app.errorhandler(403)
    def _forbidden(e):
        return render_template("error.html", code=403, message=str(e.description)), 403

    @app.errorhandler(413)
    def _too_large(e):
        return render_template(
            "error.html",
            code=413,
            message=f"File exceeds the {app.config['MAX_CONTENT_LENGTH']:,}-byte limit.",
        ), 413

    return app


def main() -> None:
    """Dev server entry point: python -m upload_app."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    app = create_app()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5005"))
    logger.info("Starting upload_app on %s:%d", host, port)
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
