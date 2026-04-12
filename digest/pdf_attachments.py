"""Glue between the Thursday digest and the pdf_store / pending_fetch tables.

The Wednesday orchestrator (scripts/prepare_week_pdfs.py) populates
pdf_store with fetched PDFs and pending_fetch with misses. This module
consumes those tables at digest send time:

    enrich_must_reads_with_pdfs(must_reads, conn, upload_base_url)
        → for each must-read, set item['pdf_status'] and item['proxy_url'],
          and return a list of (filename, pdf_bytes) attachments ready to
          hand to sender.send().

    finalize_pdf_statuses(conn, week_start, attached_ids, cutoff_ids)
        → flip pending_fetch rows to 'attached' or 'cutoff' after the
          digest is out the door.

Design notes:
- We only attach PDFs whose license is in enrich.oa_pdf.ATTACHABLE_LICENSES.
  This prevents us from distributing bronze/unknown-license content.
- If a PDF record exists in pdf_store but the file is missing from disk
  (manual deletion / moved volume), we downgrade to proxy_only rather
  than crashing the digest.
- Missing upload_base_url means no proxy/upload link is rendered;
  pdf_status still flows through so templates can show the right badge.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Iterable, List, Mapping, MutableMapping, Optional, Tuple

from digest.alerts import build_emory_proxy_url
from enrich.oa_pdf import is_attachable_license
from storage.pdf_tracking import get_pdf_record, mark_attached, mark_cutoff

logger = logging.getLogger(__name__)

Attachment = Tuple[str, bytes]

# Statuses written into each must-read dict:
#   "attached"    — PDF will be attached to the email
#   "proxy_only"  — we know about it but can't redistribute; show proxy link
#   "none"        — no PDF info at all (no pdf_store row, no original_url)
STATUS_ATTACHED = "attached"
STATUS_PROXY_ONLY = "proxy_only"
STATUS_NONE = "none"


def _slug(pub_id: str) -> str:
    """Filesystem-safe slug for email attachment filenames."""
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in pub_id)


def _original_url(item: Mapping) -> Optional[str]:
    for key in ("canonical_url", "url"):
        v = item.get(key)
        if v:
            return v
    if item.get("doi"):
        return f"https://doi.org/{item['doi']}"
    if item.get("pmid"):
        return f"https://pubmed.ncbi.nlm.nih.gov/{item['pmid']}/"
    return None


def _read_pdf_bytes(file_path: str) -> Optional[bytes]:
    """Read a PDF from disk; return None on I/O error.

    We don't want a missing-file glitch to brick the Thursday digest —
    the OA-PDF feature is additive. Log loudly and fall back to proxy.
    """
    try:
        with open(file_path, "rb") as f:
            return f.read()
    except OSError as e:
        logger.warning("pdf_store row references unreadable file %s: %s", file_path, e)
        return None


def enrich_must_reads_with_pdfs(
    must_reads: Iterable[MutableMapping],
    conn,
    upload_base_url: Optional[str] = None,
) -> List[Attachment]:
    """Add pdf_status + proxy_url to each must-read; collect attachments.

    Mutates each item dict in-place. Returns the list of (filename, bytes)
    attachments ready to pass to sender.send().
    """
    attachments: List[Attachment] = []

    for item in must_reads:
        pub_id = item.get("id") or item.get("publication_id")
        if not pub_id:
            item["pdf_status"] = STATUS_NONE
            continue

        record = get_pdf_record(conn, pub_id) if conn is not None else None

        # Always compute a proxy URL if we have any paper link — useful
        # for both 'proxy_only' fallback and for 'attached' rows (as a
        # backup if Gmail strips the attachment for a recipient).
        original = _original_url(item)
        if original:
            item["proxy_url"] = build_emory_proxy_url(original)
        else:
            item["proxy_url"] = None

        if record and is_attachable_license(record.get("license")):
            pdf_bytes = _read_pdf_bytes(record["file_path"])
            if pdf_bytes:
                filename = f"{_slug(pub_id)}.pdf"
                attachments.append((filename, pdf_bytes))
                item["pdf_status"] = STATUS_ATTACHED
                item["pdf_license"] = record.get("license")
                item["pdf_source"] = record.get("source_api")
                continue

        # Either no pdf_store row, non-attachable license, or file
        # missing from disk — fall back to proxy link.
        if item["proxy_url"]:
            item["pdf_status"] = STATUS_PROXY_ONLY
            if record:
                item["pdf_license"] = record.get("license")
        else:
            item["pdf_status"] = STATUS_NONE

    logger.info(
        "PDF enrichment complete: %d attachments prepared",
        len(attachments),
    )
    return attachments


def finalize_pdf_statuses(
    conn,
    week_start: date,
    must_reads: Iterable[Mapping],
) -> dict:
    """Flip pending_fetch rows to 'attached' or 'cutoff' after digest send.

    Called after sender.send() succeeds. Must-reads marked 'attached' get
    pending_fetch.status = 'attached' (pdf_store row exists, was sent);
    must-reads marked 'proxy_only' or 'none' get 'cutoff' if there's a
    pending row to update (no-op otherwise).
    """
    attached = 0
    cutoffs = 0
    for item in must_reads:
        pub_id = item.get("id") or item.get("publication_id")
        if not pub_id:
            continue
        status = item.get("pdf_status")
        if status == STATUS_ATTACHED:
            mark_attached(conn, pub_id, week_start)
            attached += 1
        elif status in (STATUS_PROXY_ONLY, STATUS_NONE):
            # Idempotent: mark_cutoff WHERE status IN ('pending','uploaded')
            mark_cutoff(conn, pub_id, week_start)
            cutoffs += 1

    logger.info(
        "pending_fetch finalized for %s: attached=%d cutoff=%d",
        week_start, attached, cutoffs,
    )
    return {"attached": attached, "cutoff": cutoffs}
