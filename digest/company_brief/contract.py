"""Shared Company Brief section contract.

Every section — whether sourced in-process (Science) or over HTTP (Grant,
Regulatory) — conforms to :class:`BriefSection`. The template renders only a
known subset of ``item.meta`` keys (see ``RENDERED_META_KEYS``); unknown keys
are ignored, so each tool can attach whatever it has without breaking the
render. An empty ``items`` list is valid and renders a "no new items" card.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

# tool_id values, used by the template for any per-section styling hooks.
SCIENCE = "science_agent"
GRANT = "grant_agent"
REGULATORY = "regulatory_tool"
BUSINESS = "business_intelligence"

# meta keys the template knows how to render as pills / sublines. Anything
# else on item["meta"] is carried through but not displayed.
RENDERED_META_KEYS = (
    "badge",
    "deadline",
    "days_to_deadline",
    "score",
    "funder",
    "impact",
    "source",
    "next_step",
)


class BriefItem(TypedDict, total=False):
    title: str
    summary: str
    url: Optional[str]
    meta: Dict[str, Any]


class BriefSection(TypedDict, total=False):
    tool_id: str
    section_title: str
    period_start: str
    period_end: str
    narrative: Optional[str]
    items: List[BriefItem]


def empty_section(
    tool_id: str,
    section_title: str,
    period_start: str,
    period_end: str,
) -> BriefSection:
    """A well-formed section with no items.

    Returned when a source is empty, unreachable, or errors — so the brief
    always renders all three sections and never raises on one bad feed.
    """
    return {
        "tool_id": tool_id,
        "section_title": section_title,
        "period_start": period_start,
        "period_end": period_end,
        "narrative": None,
        "items": [],
    }


def normalize_section(raw: Dict[str, Any], *, fallback: BriefSection) -> BriefSection:
    """Coerce an untrusted dict (e.g. an HTTP response) into a BriefSection.

    Missing/garbled fields fall back to ``fallback`` so a partial or unexpected
    payload degrades to an empty (but valid) section rather than crashing the
    render. Item shape is sanitized: title/summary become strings, url passes
    through, meta is forced to a dict.
    """
    if not isinstance(raw, dict):
        return fallback

    items: List[BriefItem] = []
    for it in raw.get("items") or []:
        if not isinstance(it, dict):
            continue
        meta = it.get("meta")
        items.append(
            {
                "title": str(it.get("title") or "").strip() or "Untitled",
                "summary": str(it.get("summary") or "").strip(),
                "url": it.get("url"),
                "meta": meta if isinstance(meta, dict) else {},
            }
        )

    return {
        "tool_id": str(raw.get("tool_id") or fallback["tool_id"]),
        "section_title": str(raw.get("section_title") or fallback["section_title"]),
        "period_start": str(raw.get("period_start") or fallback["period_start"]),
        "period_end": str(raw.get("period_end") or fallback["period_end"]),
        "narrative": (raw.get("narrative") or None),
        "items": items,
    }
