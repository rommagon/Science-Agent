"""Tests for digest/pdf_attachments.py — the glue layer between the
Thursday digest and the pdf_store / pending_fetch tables.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

from digest.pdf_attachments import (
    STATUS_ATTACHED,
    STATUS_NONE,
    STATUS_PROXY_ONLY,
    enrich_must_reads_with_pdfs,
    finalize_pdf_statuses,
)


SMALL_PDF = b"%PDF-1.4\n" + b"x" * 500 + b"\n%%EOF"


def _item(**over):
    base = {
        "id": "pub1",
        "title": "A paper",
        "url": "https://nature.com/a",
        "doi": "10.1/x",
        "venue": "Nature",
    }
    base.update(over)
    return base


class TestEnrichAttached:
    def test_attachable_pdf_creates_attachment(self, tmp_path):
        pdf_path = tmp_path / "p.pdf"
        pdf_path.write_bytes(SMALL_PDF)

        conn = MagicMock()

        def fake_get(conn_, pub_id):
            return {
                "publication_id": pub_id,
                "file_path": str(pdf_path),
                "license": "cc-by",
                "source_api": "unpaywall",
            }

        # Patch via monkeypatch on the module
        import digest.pdf_attachments as mod
        original = mod.get_pdf_record
        mod.get_pdf_record = fake_get
        try:
            items = [_item()]
            attachments = enrich_must_reads_with_pdfs(items, conn, "https://up.x")
        finally:
            mod.get_pdf_record = original

        assert len(attachments) == 1
        filename, data = attachments[0]
        assert filename == "pub1.pdf"
        assert data == SMALL_PDF
        assert items[0]["pdf_status"] == STATUS_ATTACHED
        assert items[0]["pdf_license"] == "cc-by"
        assert items[0]["pdf_source"] == "unpaywall"
        # Proxy URL always populated when an original URL exists
        assert "login.proxy.library.emory.edu" in items[0]["proxy_url"]

    def test_non_attachable_license_falls_back_to_proxy(self, tmp_path, monkeypatch):
        pdf_path = tmp_path / "p.pdf"
        pdf_path.write_bytes(SMALL_PDF)

        import digest.pdf_attachments as mod
        monkeypatch.setattr(mod, "get_pdf_record", lambda c, pid: {
            "publication_id": pid,
            "file_path": str(pdf_path),
            "license": "bronze",
            "source_api": "unpaywall",
        })

        items = [_item()]
        attachments = enrich_must_reads_with_pdfs(items, MagicMock(), "https://up.x")

        assert attachments == []
        assert items[0]["pdf_status"] == STATUS_PROXY_ONLY
        assert items[0]["pdf_license"] == "bronze"
        assert items[0]["proxy_url"] is not None

    def test_missing_file_on_disk_downgrades_to_proxy(self, monkeypatch):
        import digest.pdf_attachments as mod
        monkeypatch.setattr(mod, "get_pdf_record", lambda c, pid: {
            "publication_id": pid,
            "file_path": "/tmp/definitely-not-here.pdf",
            "license": "cc-by",
            "source_api": "unpaywall",
        })

        items = [_item()]
        attachments = enrich_must_reads_with_pdfs(items, MagicMock(), "https://up.x")

        assert attachments == []
        assert items[0]["pdf_status"] == STATUS_PROXY_ONLY

    def test_no_pdf_record_and_no_url_is_none(self, monkeypatch):
        import digest.pdf_attachments as mod
        monkeypatch.setattr(mod, "get_pdf_record", lambda c, pid: None)

        # No url, no doi, no pmid — should be STATUS_NONE (nothing to link to)
        items = [{"id": "pub1", "title": "x"}]
        attachments = enrich_must_reads_with_pdfs(items, MagicMock(), "https://up.x")

        assert attachments == []
        assert items[0]["pdf_status"] == STATUS_NONE
        assert items[0]["proxy_url"] is None

    def test_no_pdf_record_but_has_url_is_proxy(self, monkeypatch):
        import digest.pdf_attachments as mod
        monkeypatch.setattr(mod, "get_pdf_record", lambda c, pid: None)

        items = [_item()]
        attachments = enrich_must_reads_with_pdfs(items, MagicMock(), "https://up.x")

        assert attachments == []
        assert items[0]["pdf_status"] == STATUS_PROXY_ONLY
        assert items[0]["proxy_url"].startswith("https://login.proxy.library.emory.edu")

    def test_item_without_id_is_skipped_as_none(self):
        items = [{"title": "no id"}]
        attachments = enrich_must_reads_with_pdfs(items, MagicMock(), "https://up.x")
        assert attachments == []
        assert items[0]["pdf_status"] == STATUS_NONE


class TestFinalizeStatuses:
    def test_attached_triggers_mark_attached(self, monkeypatch):
        import digest.pdf_attachments as mod
        calls = {"attached": [], "cutoff": []}
        monkeypatch.setattr(mod, "mark_attached",
                            lambda c, pid, ws: calls["attached"].append((pid, ws)))
        monkeypatch.setattr(mod, "mark_cutoff",
                            lambda c, pid, ws: calls["cutoff"].append((pid, ws)))

        items = [
            {"id": "a", "pdf_status": STATUS_ATTACHED},
            {"id": "b", "pdf_status": STATUS_PROXY_ONLY},
            {"id": "c", "pdf_status": STATUS_NONE},
        ]
        week_start = date(2026, 4, 13)
        result = finalize_pdf_statuses(MagicMock(), week_start, items)

        assert result == {"attached": 1, "cutoff": 2}
        assert calls["attached"] == [("a", week_start)]
        assert ("b", week_start) in calls["cutoff"]
        assert ("c", week_start) in calls["cutoff"]

    def test_item_without_id_is_skipped(self, monkeypatch):
        import digest.pdf_attachments as mod
        monkeypatch.setattr(mod, "mark_attached", lambda *a: None)
        monkeypatch.setattr(mod, "mark_cutoff", lambda *a: None)

        items = [{"pdf_status": STATUS_ATTACHED}]  # no id
        result = finalize_pdf_statuses(MagicMock(), date(2026, 4, 13), items)
        assert result == {"attached": 0, "cutoff": 0}
