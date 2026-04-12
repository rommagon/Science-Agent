"""Tests for PDF attachment support in digest/senders.py and the
alert-email helper in digest/alerts.py.
"""

from email import message_from_bytes
from unittest.mock import MagicMock, patch

import pytest

from digest.alerts import (
    build_emory_proxy_url,
    build_upload_url,
    send_fetch_alert,
    _render_alert,
)
from digest.senders import DemoSender, GmailSender


SMALL_PDF = b"%PDF-1.4\n" + b"x" * 500 + b"\n%%EOF"


# --- DemoSender reports attachments ------------------------------------

class TestDemoSenderAttachments:
    def test_no_attachments_backward_compat(self, capsys):
        s = DemoSender()
        res = s.send(to=["x@y"], subject="s", html_content="<p>h</p>", text_content="t")
        assert res["success"] is True
        assert res["details"]["attachment_count"] == 0
        out = capsys.readouterr().out
        assert "Attachments: 0" in out

    def test_attachments_listed(self, capsys):
        s = DemoSender()
        res = s.send(
            to=["x@y"], subject="s", html_content="<p>h</p>", text_content="t",
            attachments=[("paper1.pdf", SMALL_PDF), ("paper2.pdf", SMALL_PDF)],
        )
        assert res["details"]["attachment_count"] == 2
        out = capsys.readouterr().out
        assert "Attachments: 2" in out
        assert "paper1.pdf" in out
        assert "paper2.pdf" in out


# --- GmailSender builds correct MIME structure ------------------------

class TestGmailSenderAttachments:
    def _mk(self):
        return GmailSender(gmail_address="me@x.com", app_password="appp")

    def test_no_attachments_keeps_alternative_root(self):
        sender = self._mk()
        captured = {}

        class FakeSMTP:
            def __init__(self, *a, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def login(self, *a): pass
            def sendmail(self, frm, to, body):
                captured["body"] = body

        with patch("smtplib.SMTP_SSL", FakeSMTP):
            sender.send(["a@b"], "s", "<p>h</p>", "t")
        msg = message_from_bytes(captured["body"])
        assert msg.get_content_type() == "multipart/alternative"

    def test_with_attachments_uses_mixed_root(self):
        sender = self._mk()
        captured = {}

        class FakeSMTP:
            def __init__(self, *a, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def login(self, *a): pass
            def sendmail(self, frm, to, body):
                captured["body"] = body

        with patch("smtplib.SMTP_SSL", FakeSMTP):
            sender.send(
                ["a@b"], "s", "<p>h</p>", "t",
                attachments=[("paper.pdf", SMALL_PDF)],
            )
        msg = message_from_bytes(captured["body"])
        assert msg.get_content_type() == "multipart/mixed"

        parts = list(msg.walk())
        # Expect: outer mixed, inner alternative, plain, html, pdf attachment
        content_types = [p.get_content_type() for p in parts]
        assert "application/pdf" in content_types
        assert "multipart/alternative" in content_types

        # Verify filename preserved
        pdf_part = [p for p in parts if p.get_content_type() == "application/pdf"][0]
        assert pdf_part.get_filename() == "paper.pdf"
        assert pdf_part.get_payload(decode=True) == SMALL_PDF


# --- Alert helper ------------------------------------------------------

class TestProxyUrl:
    def test_wraps_url(self):
        out = build_emory_proxy_url("https://nature.com/a?b=1")
        assert out.startswith("https://login.proxy.library.emory.edu/login?url=")
        # Original URL should be percent-encoded (unsafe chars only)
        assert "https%3A%2F%2Fnature.com" in out

    def test_upload_url_strips_trailing_slash(self):
        assert build_upload_url("https://x.com/", "abc") == "https://x.com/upload/abc"
        assert build_upload_url("https://x.com", "abc") == "https://x.com/upload/abc"


class TestRenderAlert:
    def _item(self, **over):
        base = {
            "publication_id": "pub1",
            "title": "A paper about X",
            "original_url": "https://nature.com/a",
            "doi": "10.1/x",
            "venue": "Nature",
        }
        base.update(over)
        return base

    def test_subject_singular_vs_plural(self):
        subj, _, _ = _render_alert([self._item()], "https://up.x")
        assert "1 must-read PDF " in subj

        subj2, _, _ = _render_alert([self._item(), self._item(publication_id="p2")], "https://up.x")
        assert "2 must-read PDFs" in subj2

    def test_reminder_prefix(self):
        subj, html, text = _render_alert([self._item()], "https://up.x", is_reminder=True)
        assert subj.startswith("[REMINDER]")
        assert "Reminder" in html
        assert "REMINDER" in text

    def test_html_has_proxy_and_upload(self):
        _, html, _ = _render_alert([self._item()], "https://up.x")
        assert "login.proxy.library.emory.edu" in html
        assert "https://up.x/upload/pub1" in html
        assert "A paper about X" in html

    def test_escapes_html(self):
        item = self._item(title="<script>bad</script>")
        _, html, _ = _render_alert([item], "https://up.x")
        assert "<script>bad</script>" not in html
        assert "&lt;script&gt;" in html

    def test_handles_missing_original_url(self):
        item = self._item(original_url=None, doi=None, venue=None)
        _, html, text = _render_alert([item], "https://up.x")
        # Should not have an "Open via Emory proxy" link
        assert "Emory proxy" not in html
        assert "Upload PDF" in html


class TestSendFetchAlert:
    def test_empty_is_noop(self):
        sender = MagicMock()
        res = send_fetch_alert(sender, ["a@b"], [], "https://up.x")
        assert res["success"] is True
        assert res.get("skipped") is True
        sender.send.assert_not_called()

    def test_sends_when_items_present(self):
        sender = MagicMock()
        sender.send.return_value = {"success": True, "message": "ok", "details": {}}
        res = send_fetch_alert(
            sender,
            ["a@b"],
            [{"publication_id": "p1", "title": "t", "original_url": "https://x"}],
            "https://up.x",
        )
        assert res["success"] is True
        sender.send.assert_called_once()
        kwargs = sender.send.call_args.kwargs
        assert kwargs["to"] == ["a@b"]
        assert "1 must-read" in kwargs["subject"]
