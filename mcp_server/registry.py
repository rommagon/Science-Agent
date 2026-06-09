"""Shared MCP tool registry consumed by both the stdio and HTTP transports.

Defining the tools and dispatch logic in one place keeps the two transports
(``mcp_server.server`` for stdio / OpenAI Apps SDK, ``mcp_server.http_app`` for
remote claude.ai connectors) in lockstep.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.types import TextContent, Tool

from mcp_server.must_reads import get_must_reads_from_db
from mcp_server.tools import get_publication_tool, search_publications_tool

logger = logging.getLogger(__name__)


TOOLS: list[Tool] = [
    Tool(
        name="search_publications",
        description=(
            "Find publications in the SpotitEarly Science Agent corpus that are "
            "semantically relevant to a topic, draft excerpt, or research question. "
            "Returns ranked publications with titles, AI summaries, tri-model relevancy "
            "and credibility scores, and a best-resolvable link. Use this to ground "
            "knowledge-center articles in our curated literature."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Topic, question, or draft paragraph to find relevant "
                        "publications for. Free text; longer queries with concrete "
                        "terms work better than single keywords."
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default 10, max 25).",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 25,
                },
                "since_days": {
                    "type": "integer",
                    "description": (
                        "Restrict to publications from the last N days "
                        "(default 365). Pass 0 to disable the date filter."
                    ),
                    "default": 365,
                    "minimum": 0,
                },
                "min_relevancy_score": {
                    "type": "number",
                    "description": (
                        "Optional 0-100 threshold; drop publications whose "
                        "stored final_relevancy_score is below this value."
                    ),
                    "minimum": 0,
                    "maximum": 100,
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="get_publication",
        description=(
            "Fetch the full record for a single publication by id, including the "
            "abstract (raw_text), AI summary, per-model and consensus scores, "
            "credibility info, and best resolvable link. Use this after "
            "search_publications when you need the full text or scoring rationale."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "publication_id": {
                    "type": "string",
                    "description": "Publication id (SHA256 hash) returned by search_publications.",
                },
            },
            "required": ["publication_id"],
        },
    ),
    Tool(
        name="get_must_reads",
        description=(
            "Retrieve the most important recent publications from the corpus. "
            "Returns ranked publications with key findings and relevance scores."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "since_days": {
                    "type": "integer",
                    "description": "Number of days to look back (default 7).",
                    "default": 7,
                    "minimum": 1,
                    "maximum": 90,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of must-reads to return (default 10).",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 50,
                },
                "use_ai": {
                    "type": "boolean",
                    "description": "Use AI reranking if OPENAI_API_KEY is available (default true).",
                    "default": True,
                },
                "rerank_max_candidates": {
                    "type": "integer",
                    "description": "Maximum candidates passed to the AI reranker (default 25).",
                    "default": 25,
                    "minimum": 10,
                    "maximum": 200,
                },
            },
            "required": [],
        },
    ),
]


async def dispatch(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    """Run a tool and return its result wrapped as MCP TextContent.

    Raises:
        ValueError: if ``name`` is not a registered tool.
    """
    args = arguments or {}
    logger.info("MCP tool call: %s args=%s", name, list(args.keys()))

    if name == "search_publications":
        since = args.get("since_days", 365)
        result: Any = search_publications_tool(
            query=args.get("query", ""),
            top_k=args.get("top_k", 10),
            since_days=since if since else None,
            min_relevancy_score=args.get("min_relevancy_score"),
        )
    elif name == "get_publication":
        result = get_publication_tool(publication_id=args.get("publication_id", ""))
    elif name == "get_must_reads":
        result = get_must_reads_from_db(
            since_days=args.get("since_days", 7),
            limit=args.get("limit", 10),
            use_ai=args.get("use_ai", True),
            rerank_max_candidates=args.get("rerank_max_candidates", 25),
        )
    else:
        raise ValueError(f"Unknown tool: {name}")

    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
