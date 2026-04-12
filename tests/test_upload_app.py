"""Tests for upload_app — the Flask service for manually uploading PDFs."""

import io
import os
import sqlite3
import tempfile
from datetime import date, datetime, timedelta

import pytest

flask = pytest.importorskip("flask")  # skip these tests if Flask isn't installed

from upload_app.app import CF_ACCESS_EMAIL_HEADER, create_app
from storage.sqlite_store import _init_schema


VALID_PDF = b"%PDF-1.4\n" + b"x" * 2000 + b"\n%%EOF"


def _week_start_today():
    today = datetime.utcnow().date()
    return today - timedelta(days=today.weekday())


@pytest.fixture()
def sqlite_db(tmp_path):
    db_path = tmp_path / "test.db"
    # Initialize schema (creates pdf_store + pending_fetch via v10 migration)
    conn = sqlite3.connect(str(db_path))
    _init_schema(conn)
    # Insert a pending_fetch row for this week
    ws = _week_start_today()
    conn.execute(
        """INSERT INTO pending_fetch
           (publication_id, week_start, status, original_url, alerted_at)
           VALUES (?, ?, 'pending', ?, ?)""",
        ("pub1", ws.isoformat(), "https://nature.com/a", datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    return str(db_path)


@pytest.fixture()
def pdf_dir(tmp_path):
    d = tmp_path / "pdfs"
    d.mkdir()
    return str(d)


@pytest.fixture()
def client(sqlite_db, pdf_dir):
    app = create_app({
        "DATABASE_URL": None,
        "SQLITE_PATH": sqlite_db,
        "PDF_STORE_DIR": pdf_dir,
        "ALLOWED_UPLOADER_EMAILS": ["ops@example.com"],
        "TESTING": True,
        "SECRET_KEY": "test",
    })
    return app.test_client()


def _auth(email="ops@example.com"):
    return {CF_ACCESS_EMAIL_HEADER: email}


class TestAuth:
    def test_no_header_returns_401(self, client):
        r = client.get("/pending")
        assert r.status_code == 401

    def test_wrong_email_returns_401(self, client):
        r = client.get("/pending", headers=_auth("stranger@example.com"))
        assert r.status_code == 401  # not in allow-list → treated as unauthed

    def test_healthz_is_open(self, client):
        # Liveness must not require auth — CF Tunnel needs to probe it.
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json == {"status": "ok"}


class TestPendingList:
    def test_shows_pending_row(self, client):
        r = client.get("/pending", headers=_auth())
        assert r.status_code == 200
        assert b"pub1" in r.data
        assert b"nature.com" in r.data

    def test_index_redirects(self, client):
        r = client.get("/", headers=_auth())
        assert r.status_code == 302
        assert "/pending" in r.headers["Location"]


class TestUpload:
    def test_rejects_non_pdf(self, client, pdf_dir):
        r = client.post(
            "/upload/pub1",
            headers=_auth(),
            data={"pdf": (io.BytesIO(b"<html>not a pdf</html>" + b"x" * 2000), "fake.pdf"),
                  "license": "cc-by"},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        # Flashed error after redirect
        assert b"not a PDF" in r.data or b"bad magic" in r.data
        assert os.listdir(pdf_dir) == []

    def test_rejects_tiny_file(self, client, pdf_dir):
        r = client.post(
            "/upload/pub1",
            headers=_auth(),
            data={"pdf": (io.BytesIO(b"%PDF-1.4 tiny"), "t.pdf"),
                  "license": "cc-by"},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert b"too small" in r.data.lower() or b"not a real pdf" in r.data.lower()
        assert os.listdir(pdf_dir) == []

    def test_rejects_unknown_license(self, client, pdf_dir):
        r = client.post(
            "/upload/pub1",
            headers=_auth(),
            data={"pdf": (io.BytesIO(VALID_PDF), "a.pdf"),
                  "license": "some-weird-thing"},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert b"Unknown license" in r.data
        assert os.listdir(pdf_dir) == []

    def test_happy_path(self, client, pdf_dir, sqlite_db):
        r = client.post(
            "/upload/pub1",
            headers=_auth(),
            data={"pdf": (io.BytesIO(VALID_PDF), "a.pdf"),
                  "license": "cc-by"},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert r.status_code == 200
        # Should have redirected to /pending and flashed success
        assert b"Uploaded" in r.data

        # File landed
        files = os.listdir(pdf_dir)
        assert len(files) == 1
        assert files[0].startswith("pub1-")
        assert files[0].endswith(".pdf")

        # pdf_store got a row
        conn = sqlite3.connect(sqlite_db)
        try:
            row = conn.execute(
                "SELECT publication_id, license, source_api, bytes_len FROM pdf_store WHERE publication_id='pub1'"
            ).fetchone()
            assert row == ("pub1", "cc-by", "manual-upload", len(VALID_PDF))

            # pending_fetch was flipped to 'uploaded'
            status = conn.execute(
                "SELECT status FROM pending_fetch WHERE publication_id='pub1'"
            ).fetchone()[0]
            assert status == "uploaded"
        finally:
            conn.close()

    def test_force_upload_without_pending_row(self, client, pdf_dir, sqlite_db):
        # 'pub999' has no pending_fetch row — upload should still work.
        r = client.post(
            "/upload/pub999",
            headers=_auth(),
            data={"pdf": (io.BytesIO(VALID_PDF), "a.pdf"),
                  "license": "cc-by"},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert r.status_code == 200
        conn = sqlite3.connect(sqlite_db)
        try:
            row = conn.execute(
                "SELECT publication_id FROM pdf_store WHERE publication_id='pub999'"
            ).fetchone()
            assert row is not None
        finally:
            conn.close()
