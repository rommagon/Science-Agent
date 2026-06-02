"""Assemble the three Company Brief sections in fixed order.

Order is fixed by product decision: Science -> Grant -> Regulatory. Each
source is isolated — a failure in one returns an empty section (never
raises), so a single down backend degrades gracefully instead of killing
the whole email.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from . import client
from .contract import GRANT, REGULATORY, BriefSection, empty_section
from .science_adapter import build_science_section

logger = logging.getLogger(__name__)

GRANT_SECTION_TITLE = "Grant Funding Opportunities"
REGULATORY_SECTION_TITLE = "Regulatory Updates"


@dataclass
class BriefConfig:
    """Runtime knobs, resolved from CLI args + environment."""

    grant_brief_url: Optional[str] = None
    regulatory_brief_url: Optional[str] = None
    brief_token: Optional[str] = None
    science_top_n: int = 5
    science_min_score: float = 70.0
    grant_must_apply: float = 75.0
    database_url: Optional[str] = None
    db_path: Optional[str] = None
    http_timeout: float = 15.0

    @classmethod
    def from_env(cls, **overrides) -> "BriefConfig":
        cfg = cls(
            grant_brief_url=os.environ.get("GRANT_BRIEF_URL"),
            regulatory_brief_url=os.environ.get("REGULATORY_BRIEF_URL"),
            brief_token=os.environ.get("BRIEF_TOKEN"),
            database_url=os.environ.get("DATABASE_URL"),
        )
        for key, value in overrides.items():
            if value is not None and hasattr(cfg, key):
                setattr(cfg, key, value)
        return cfg


def build_company_brief(
    week_start: date,
    week_end: date,
    cfg: BriefConfig,
) -> List[BriefSection]:
    """Return the three sections in render order. Resilient per-source."""
    ws, we = week_start.isoformat(), week_end.isoformat()

    # 1. Science — in-process (already internally try/excepted).
    science = build_science_section(
        week_start,
        week_end,
        top_n=cfg.science_top_n,
        min_relevancy_score=cfg.science_min_score,
        database_url=cfg.database_url,
        db_path=cfg.db_path,
    )

    # 2. Grant — HTTP brief feed.
    grant = client.fetch_brief(
        cfg.grant_brief_url,
        cfg.brief_token,
        tool_id=GRANT,
        section_title=GRANT_SECTION_TITLE,
        week_start=ws,
        week_end=we,
        timeout=cfg.http_timeout,
    )

    # 3. Regulatory — HTTP brief feed.
    regulatory = client.fetch_brief(
        cfg.regulatory_brief_url,
        cfg.brief_token,
        tool_id=REGULATORY,
        section_title=REGULATORY_SECTION_TITLE,
        week_start=ws,
        week_end=we,
        timeout=cfg.http_timeout,
    )

    sections = [science, grant, regulatory]
    total = sum(len(s.get("items") or []) for s in sections)
    logger.info(
        "company_brief assembled: science=%d grant=%d regulatory=%d (total=%d)",
        len(science.get("items") or []),
        len(grant.get("items") or []),
        len(regulatory.get("items") or []),
        total,
    )
    return sections
