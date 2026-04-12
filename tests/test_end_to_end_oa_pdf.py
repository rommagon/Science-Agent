"""End-to-end dry-run of the OA-PDF pipeline against a fresh SQLite DB.

Exercises the full loop without touching the network or the production
Postgres — so it's safe to run in CI. The goal is to catch wiring
regressions between the scripts, storage layer, alerts, and upload app
before they reach prod.

Flow:
    1. Bootstrap a fresh SQLite DB with the latest schema (v10 adds
       pdf_store + pending_fetch).
    2. Insert two fake must-read publications: one whose OA cascade will
       "succeed" (cc-by PDF) and one that will "miss".
    3. Patch enrich.oa_pdf.fetch_oa_pdf to return deterministic results
       instead of hitting the network.
    4. Run the Wednesday orchestrator end-to-end.
    5. Run the digest enrichment + finalize step.
    6. Upload the missing paper's PDF via the Flask test client.
    7. Re-run the reminder flow and confirm it's now a no-op.
"""

from __future__ import annotations

import io
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

flask = pytest.importorskip("flask")

from digest.pdf_attachments import (
    STATUS_ATTACHED,
    STATUS_PROXY_ONLY,
    enrich_must_reads_with_pdfs,
    finalize_pdf_statuses,
)
from enrich.oa_pdf import OaPdfResult
from scripts.prepare_week_pdfs import collect_pending_for_reminder, prepare_week
from storage.pdf_tracking import get_connection
from storage.sqlite_store import _init_schema
from upload_app.app import CF_ACCESS_EMAIL_HEADER, create_app


SMALL_PDF = b"%PDF-1.4\n" + b"x" * 6000 + b"\n%%EOF"


@pytest.fixture()
def week_bounds():
    today = datetime.utcnow().date()
    ws = today - timedelta(days=today.weekday())
    we = ws + timedelta(days=6)
    return ws, we


@pytest.fixture()
def db_path(tmp_path):
    """Fresh SQLite DB with the latest schema (v10 adds pdf_store + pending_fetch)."""
    path = str(tmp_path / "e2e.db")
    conn = sqlite3.connect(path)
    _init_schema(conn)
    conn.close()
    # Must-reads are supplied via the _patched_must_reads stub, not the DB,
    # so we don't need rows in the `publications` table here.
    return path


def _fake_fetch(doi=None, pmid=None, email="", session=None):
    """Stand-in for enrich.oa_pdf.fetch_oa_pdf.

    Returns a cc-by hit for DOI 10.1/attach and None for 10.1/pay.
    """
    if doi == "10.1/attach":
        return OaPdfResult(
            pdf_bytes=SMALL_PDF,
            license="cc-by",
            source_api="unpaywall",
            source_url="https://example.com/a.pdf",
        )
    return None


def _patched_must_reads(ws, we):
    """Stub for get_publications_for_week — returns our two fakes."""
    return {
        "must_reads": [
            {
                "id": "attachable-1",
                "publication_id": "attachable-1",
                "title": "Attachable paper",
                "url": "https://example.com/a",
                "doi": "10.1/attach",
                "venue": "Example",
                "published_date": "2026-04-14",
                "source": "test-src",
                "relevancy_score": 85,
            },
            {
                "id": "paywalled-1",
                "publication_id": "paywalled-1",
                "title": "Paywalled paper",
                "url": "https://nature.com/b",
                "doi": "10.1/pay",
                "venue": "Nature",
                "published_date": "2026-04-14",
                "source": "test-src",
                "relevancy_score": 80,
            },
        ],
        "honorable_mentions": [],
        "total_candidates": 2,
        "scoring_method": "test",
    }


def test_end_to_end_happy_path(tmp_path, db_path, week_bounds):
    ws, we = week_bounds
    pdf_dir = tmp_path / "pdfs"

    # --- Phase 1: Wednesday orchestrator -----------------------------
    with patch("scripts.prepare_week_pdfs.get_publications_for_week",
               side_effect=lambda **kw: _patched_must_reads(kw["week_start"], kw["week_end"])), \
         patch("scripts.prepare_week_pdfs.fetch_oa_pdf", side_effect=_fake_fetch):
        summary = prepare_week(
            week_start=ws, week_end=we,
            pdf_dir=pdf_dir,
            upload_base_url="https://ai.spotitearly.com",
            unpaywall_email="me@x.com",
            top_n=2, min_score=0,
            dry_run=False,
            db_path=db_path, database_url=None,
        )

    assert len(summary["fetched"]) == 1
    assert len(summary["pending"]) == 1
    assert summary["fetched"][0]["publication_id"] == "attachable-1"
    assert summary["pending"][0]["publication_id"] == "paywalled-1"

    # PDF landed on disk under <dir>/<slug>-<sha12>.pdf
    disk_files = list(pdf_dir.glob("attachable-1-*.pdf"))
    assert len(disk_files) == 1
    assert disk_files[0].read_bytes() == SMALL_PDF

    # DB state: pdf_store has the attachable row; pending_fetch has the miss
    conn = sqlite3.connect(db_path)
    try:
        pdf_rows = conn.execute("SELECT publication_id, license FROM pdf_store").fetchall()
        assert pdf_rows == [("attachable-1", "cc-by")]

        pending = conn.execute(
            "SELECT publication_id, status FROM pending_fetch WHERE week_start=?",
            (ws.isoformat(),),
        ).fetchall()
        assert pending == [("paywalled-1", "pending")]
    finally:
        conn.close()

    # --- Phase 2: Thursday digest enrichment -------------------------
    must_reads = _patched_must_reads(ws, we)["must_reads"]
    conn = get_connection(sqlite_path=db_path)
    try:
        attachments = enrich_must_reads_with_pdfs(
            must_reads, conn, upload_base_url="https://ai.spotitearly.com",
        )
    finally:
        conn.close()

    assert len(attachments) == 1
    fname, data = attachments[0]
    assert fname == "attachable-1.pdf"
    assert data == SMALL_PDF

    statuses = [(it["id"], it["pdf_status"]) for it in must_reads]
    assert statuses == [
        ("attachable-1", STATUS_ATTACHED),
        ("paywalled-1", STATUS_PROXY_ONLY),
    ]
    # Proxy URL for the paywalled one
    assert must_reads[1]["proxy_url"].startswith("https://login.proxy.library.emory.edu")

    # --- Phase 3: finalize statuses after "send" ---------------------
    conn = get_connection(sqlite_path=db_path)
    try:
        result = finalize_pdf_statuses(conn, ws, must_reads)
    finally:
        conn.close()
    assert result == {"attached": 1, "cutoff": 1}

    conn = sqlite3.connect(db_path)
    try:
        statuses = dict(conn.execute(
            "SELECT publication_id, status FROM pending_fetch WHERE week_start=?",
            (ws.isoformat(),),
        ).fetchall())
    finally:
        conn.close()
    # attachable-1 had no pending row, so only paywalled-1 was updated
    assert statuses == {"paywalled-1": "cutoff"}

    # --- Phase 4: upload app uploads the missing one -----------------
    # First re-insert paywalled-1 as 'pending' (simulating the Wed run
    # a week from now — pending row would still be fresh).
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE pending_fetch SET status='pending' WHERE publication_id='paywalled-1'"
        )
        conn.commit()
    finally:
        conn.close()

    app = create_app({
        "DATABASE_URL": None,
        "SQLITE_PATH": db_path,
        "PDF_STORE_DIR": str(pdf_dir),
        "ALLOWED_UPLOADER_EMAILS": ["ops@x.com"],
        "TESTING": True,
        "SECRET_KEY": "test",
    })
    client = app.test_client()

    # Before upload: pending_list shows the row
    r = client.get("/pending", headers={CF_ACCESS_EMAIL_HEADER: "ops@x.com"})
    assert r.status_code == 200
    assert b"paywalled-1" in r.data

    # Upload
    r = client.post(
        "/upload/paywalled-1",
        headers={CF_ACCESS_EMAIL_HEADER: "ops@x.com"},
        data={"pdf": (io.BytesIO(SMALL_PDF), "b.pdf"), "license": "cc-by-nc"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert b"Uploaded" in r.data

    # pdf_store now has both papers; pending_fetch flipped to 'uploaded'
    conn = sqlite3.connect(db_path)
    try:
        pdf_ids = [r[0] for r in conn.execute(
            "SELECT publication_id FROM pdf_store ORDER BY publication_id"
        ).fetchall()]
        assert pdf_ids == ["attachable-1", "paywalled-1"]
        assert conn.execute(
            "SELECT status FROM pending_fetch WHERE publication_id='paywalled-1'"
        ).fetchone()[0] == "uploaded"
    finally:
        conn.close()

    # --- Phase 5: Thursday reminder would be a no-op -----------------
    # Only reset the Wednesday-style 'pending' rows are considered; after
    # upload, nothing is 'pending' anymore.
    with patch("scripts.prepare_week_pdfs.get_publications_for_week",
               side_effect=lambda **kw: _patched_must_reads(kw["week_start"], kw["week_end"])):
        reminder_items = collect_pending_for_reminder(
            week_start=ws, week_end=we, top_n=2, min_score=0,
            db_path=db_path, database_url=None,
        )
    assert reminder_items == []
