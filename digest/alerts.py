"""Alert emails for the OA-PDF pipeline.

send_fetch_alert(): Wednesday alert listing papers whose automatic OA-PDF
retrieval failed. Includes a pre-built Emory library proxy link for each
paper plus an upload link back to the internal upload app.

The alert is plain and minimal by design — it's a to-do list for one
human (the founder), not a public communication.
"""

from __future__ import annotations

import logging
from typing import Iterable, List, Mapping, Optional, Sequence
from urllib.parse import quote

from digest.senders import EmailSender

logger = logging.getLogger(__name__)

EMORY_PROXY_PREFIX = "https://login.proxy.library.emory.edu/login?url="


def build_emory_proxy_url(original_url: str) -> str:
    """Wrap a publication URL in Emory's library EZproxy login redirect."""
    return f"{EMORY_PROXY_PREFIX}{quote(original_url, safe='')}"


def build_upload_url(upload_base_url: str, publication_id: str) -> str:
    """Build the per-publication upload URL in the internal upload app."""
    return f"{upload_base_url.rstrip('/')}/upload/{publication_id}"


def _render_alert(
    pending_items: Sequence[Mapping],
    upload_base_url: str,
    is_reminder: bool = False,
) -> tuple[str, str, str]:
    """Render the alert as (subject, html_content, text_content).

    pending_items is a list of dicts with keys:
        publication_id, title, original_url, doi (optional), venue (optional)
    """
    n = len(pending_items)
    prefix = "[REMINDER] " if is_reminder else ""
    subject = f"{prefix}{n} must-read PDF{'s' if n != 1 else ''} need manual fetch"

    html_rows: List[str] = []
    text_rows: List[str] = []
    for item in pending_items:
        title = item.get("title", "(untitled)")
        pub_id = item["publication_id"]
        original = item.get("original_url") or ""
        doi = item.get("doi")
        venue = item.get("venue")

        proxy = build_emory_proxy_url(original) if original else ""
        upload = build_upload_url(upload_base_url, pub_id)

        meta_parts = [p for p in (venue, doi) if p]
        meta = f" — {' · '.join(meta_parts)}" if meta_parts else ""

        html_rows.append(
            f'<li style="margin-bottom:16px">'
            f'<strong>{_html_escape(title)}</strong>'
            f'<span style="color:#666">{_html_escape(meta)}</span><br>'
            + (f'<a href="{_html_escape(proxy)}">Open via Emory proxy</a> &middot; ' if proxy else "")
            + f'<a href="{_html_escape(upload)}">Upload PDF</a>'
            + (f' &middot; <a href="{_html_escape(original)}">Original link</a>' if original else "")
            + '</li>'
        )
        text_lines = [f"- {title}{meta}"]
        if proxy:
            text_lines.append(f"  Emory proxy: {proxy}")
        if original:
            text_lines.append(f"  Original:    {original}")
        text_lines.append(f"  Upload:      {upload}")
        text_rows.append("\n".join(text_lines))

    intro_html = (
        "<p>These must-reads could not be retrieved automatically "
        "from open-access sources. Please fetch the PDFs so the "
        "Thursday digest can include them.</p>"
    )
    if is_reminder:
        intro_html = (
            "<p><strong>Reminder:</strong> the Thursday digest sends soon. "
            "Please upload PDFs for the papers below, or they will go out "
            "with Emory proxy links only.</p>"
        )

    html_content = (
        "<!DOCTYPE html><html><body style='font-family:Arial,sans-serif;max-width:640px'>"
        + intro_html
        + "<ul style='list-style:none;padding-left:0'>"
        + "".join(html_rows)
        + "</ul>"
        + "<p style='color:#999;font-size:12px'>Sent by the OA-PDF pipeline "
        "(acitracker Wednesday prepare step).</p>"
        + "</body></html>"
    )

    text_content = (
        ("REMINDER: Thursday digest sends soon.\n\n" if is_reminder else "")
        + f"{n} must-read{'s' if n != 1 else ''} need manual PDF fetch:\n\n"
        + "\n\n".join(text_rows)
        + "\n\n— acitracker OA-PDF pipeline\n"
    )

    return subject, html_content, text_content


def _html_escape(s: Optional[str]) -> str:
    if not s:
        return ""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def send_fetch_alert(
    sender: EmailSender,
    recipients: Iterable[str],
    pending_items: Sequence[Mapping],
    upload_base_url: str,
    is_reminder: bool = False,
) -> dict:
    """Send the fetch-alert email.

    Returns the sender's result dict. Returns {'success': True, 'skipped': True}
    without sending when pending_items is empty (idempotent reminder cron).
    """
    if not pending_items:
        logger.info("No pending fetch items — skipping alert send")
        return {"success": True, "skipped": True, "message": "no pending items"}

    recipients = list(recipients)
    subject, html, text = _render_alert(pending_items, upload_base_url, is_reminder)
    result = sender.send(
        to=recipients,
        subject=subject,
        html_content=html,
        text_content=text,
    )
    logger.info(
        "Fetch alert sent: n=%d reminder=%s recipients=%s",
        len(pending_items),
        is_reminder,
        recipients,
    )
    return result
