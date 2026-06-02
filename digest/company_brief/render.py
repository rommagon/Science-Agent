"""Render the Company Brief sections to (html, text) via Jinja2.

Templates live in ``acitracker_v1/templates/`` alongside the existing
weekly-digest templates: ``company_brief.html.j2`` + ``company_brief.txt.j2``.
"""

from __future__ import annotations

import os
from datetime import date
from typing import List, Optional, Tuple

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .contract import BriefSection


def _templates_dir() -> str:
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "templates",
    )


def render_company_brief(
    sections: List[BriefSection],
    week_start: date,
    week_end: date,
    *,
    templates_dir: Optional[str] = None,
) -> Tuple[str, str]:
    """Return ``(html, text)`` for the brief.

    Autoescape is on for the HTML template; the text template is rendered
    with the same context but escaping is harmless for plain text.
    """
    env = Environment(
        loader=FileSystemLoader(templates_dir or _templates_dir()),
        autoescape=select_autoescape(enabled_extensions=("j2", "html")),
        trim_blocks=True,
        lstrip_blocks=True,
    )

    ctx = {
        "sections": sections,
        "period_start": week_start.strftime("%b %-d, %Y"),
        "period_end": week_end.strftime("%b %-d, %Y"),
        "period_start_iso": week_start.isoformat(),
        "period_end_iso": week_end.isoformat(),
    }

    html = env.get_template("company_brief.html.j2").render(**ctx)
    text = env.get_template("company_brief.txt.j2").render(**ctx)
    return html, text
