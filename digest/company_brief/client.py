"""HTTP client for per-tool brief endpoints.

Each external tool (Grant, Regulatory) exposes an authenticated
``GET /api/brief`` that returns the shared section contract. On EC2 the
aggregator reaches them over the loopback interface (e.g.
``http://127.0.0.1:8105/api/brief``).

The client is deliberately resilient: any failure (timeout, non-200, bad
JSON, unreachable host) returns a well-formed *empty* section rather than
raising, so one down backend never blocks the whole brief.

Uses only the stdlib (``urllib``) to avoid adding a dependency to the
aggregator's runtime.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from .contract import BriefSection, empty_section, normalize_section

logger = logging.getLogger(__name__)


def fetch_brief(
    url: str,
    token: Optional[str],
    *,
    tool_id: str,
    section_title: str,
    week_start: str,
    week_end: str,
    timeout: float = 15.0,
) -> BriefSection:
    """Fetch one tool's brief section over HTTP.

    Args:
        url: Base brief endpoint (e.g. ``http://127.0.0.1:8105/api/brief``).
        token: Shared ``X-Brief-Token`` value (None disables the call).
        tool_id / section_title: identity used for the empty-section fallback.
        week_start / week_end: ISO dates passed as query params.
        timeout: per-request timeout in seconds.

    Returns:
        A normalized :class:`BriefSection`. Never raises — failures degrade to
        an empty section.
    """
    fallback = empty_section(tool_id, section_title, week_start, week_end)

    if not url:
        logger.warning("brief.fetch skipped: no URL for %s", tool_id)
        return fallback
    if not token:
        logger.warning("brief.fetch skipped: no BRIEF_TOKEN for %s", tool_id)
        return fallback

    query = urllib.parse.urlencode({"week_start": week_start, "week_end": week_end})
    full_url = f"{url}?{query}"
    req = urllib.request.Request(
        full_url,
        headers={"X-Brief-Token": token, "Accept": "application/json"},
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                logger.warning(
                    "brief.fetch %s returned HTTP %s", tool_id, resp.status
                )
                return fallback
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        logger.warning("brief.fetch %s HTTPError %s: %s", tool_id, exc.code, exc.reason)
        return fallback
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning("brief.fetch %s failed: %s", tool_id, exc)
        return fallback

    section = normalize_section(payload, fallback=fallback)
    logger.info(
        "brief.fetch %s ok: %d item(s)", tool_id, len(section.get("items") or [])
    )
    return section
