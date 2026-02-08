"""Helpers for signed weekly digest feedback links."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Dict
from urllib.parse import urlencode

ALLOWED_VOTES = {"up", "down"}


def _canonical_query(params: Dict[str, str]) -> str:
    """Build deterministic query string for signing."""
    normalized = {k: str(v) for k, v in params.items() if v is not None}
    return urlencode(sorted(normalized.items()), doseq=False)


def sign_feedback_params(params: Dict[str, str], secret: str) -> str:
    """Return HMAC-SHA256 hex signature for params."""
    payload = _canonical_query(params).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_feedback_signature(params: Dict[str, str], signature: str, secret: str) -> bool:
    """Validate signature against params."""
    expected = sign_feedback_params(params, secret)
    return hmac.compare_digest(expected, signature)


def build_feedback_url(
    *,
    base_url: str,
    publication_id: str,
    week_start: str,
    week_end: str,
    vote: str,
    secret: str,
    ts: int | None = None,
) -> str:
    """Build signed feedback URL for a single publication vote."""
    if vote not in ALLOWED_VOTES:
        raise ValueError(f"Invalid vote: {vote}. Must be one of {sorted(ALLOWED_VOTES)}")

    if ts is None:
        ts = int(time.time())

    params = {
        "p": publication_id,
        "w": week_start,
        "e": week_end,
        "v": vote,
        "t": str(ts),
    }
    sig = sign_feedback_params(params, secret)
    query = _canonical_query({**params, "s": sig})
    return f"{base_url.rstrip('/')}?{query}"
