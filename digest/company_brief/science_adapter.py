"""Science Agent section adapter.

Science content lives in this repo, so it's sourced in-process by reusing
``digest.data_access.get_publications_for_week`` (the same query the
standalone weekly digest uses) and normalizing the ``must_reads`` into the
shared brief-section contract — no HTTP round-trip.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

from digest.data_access import get_publications_for_week

from .contract import SCIENCE, BriefSection, empty_section

logger = logging.getLogger(__name__)

SECTION_TITLE = "Science: Cancer Early-Detection Research"


def _fmt_date(value: Any) -> Optional[str]:
    """Format a publication date as 'Mon D, YYYY' (e.g. May 29, 2026).

    Accepts a date/datetime or an ISO-ish string; returns None when there's
    nothing renderable.
    """
    if not value:
        return None
    if isinstance(value, (date, datetime)):
        d = value
    else:
        try:
            d = datetime.fromisoformat(str(value)[:10])
        except ValueError:
            return str(value)[:10] or None
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def build_science_section(
    week_start: date,
    week_end: date,
    *,
    top_n: int = 5,
    min_relevancy_score: float = 70.0,
    database_url: Optional[str] = None,
    db_path: Optional[str] = None,
) -> BriefSection:
    """Build the Science section from the publications DB.

    Mirrors the standalone digest's selection (top-N by relevancy, filtered
    by ``min_relevancy_score``). Never raises — any failure degrades to an
    empty section so the brief still ships.
    """
    fallback = empty_section(
        SCIENCE, SECTION_TITLE, week_start.isoformat(), week_end.isoformat()
    )
    try:
        data = get_publications_for_week(
            week_start=week_start,
            week_end=week_end,
            top_n=top_n,
            min_relevancy_score=min_relevancy_score,
            database_url=database_url,
            db_path=db_path,
        )
    except Exception:  # noqa: BLE001
        logger.exception("science_adapter: get_publications_for_week failed")
        return fallback

    items = []
    for pub in data.get("must_reads", []):
        score = pub.get("relevancy_score")
        items.append(
            {
                "title": pub.get("title") or "Untitled Publication",
                "summary": pub.get("why_it_matters") or pub.get("summary") or "",
                "url": pub.get("link"),
                "meta": {
                    "badge": pub.get("relevancy_ordinal"),
                    "score": round(score, 1) if isinstance(score, (int, float)) else None,
                    "source": pub.get("venue") or pub.get("source"),
                    "date": _fmt_date(pub.get("published_date")),
                },
            }
        )

    return {
        "tool_id": SCIENCE,
        "section_title": SECTION_TITLE,
        "period_start": week_start.isoformat(),
        "period_end": week_end.isoformat(),
        "narrative": None,
        "items": items,
    }
