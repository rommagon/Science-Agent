"""MCP server for acitrack with OpenAI Apps SDK integration.

This server provides tools for interacting with the acitrack publication database
through a Custom GPT with MCP integration.
"""

import asyncio
import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from mcp_server.must_reads import get_must_reads_from_db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Create MCP server
app = Server("acitrack-mcp")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools.

    Returns:
        List of available MCP tools
    """
    return [
        Tool(
            name="get_must_reads",
            description=(
                "Retrieve the most important recent publications from acitrack. "
                "Returns ranked publications with key findings and relevance scores. "
                "This tool is component-initiated and returns UI-renderable content."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "since_days": {
                        "type": "integer",
                        "description": "Number of days to look back (default: 7)",
                        "default": 7,
                        "minimum": 1,
                        "maximum": 90,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of must-reads to return (default: 10)",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 50,
                    },
                },
                "required": [],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls.

    Args:
        name: Tool name
        arguments: Tool arguments

    Returns:
        List of text content responses

    Raises:
        ValueError: If tool name is unknown
    """
    if name == "get_must_reads":
        since_days = arguments.get("since_days", 7)
        limit = arguments.get("limit", 10)

        logger.info(
            "get_must_reads called with since_days=%d, limit=%d",
            since_days,
            limit,
        )

        # Get must-reads data
        result = get_must_reads_from_db(
            since_days=since_days,
            limit=limit,
        )

        # Return as structured content for UI rendering
        import json

        return [
            TextContent(
                type="text",
                text=json.dumps(result, indent=2),
            )
        ]
    else:
        raise ValueError(f"Unknown tool: {name}")


async def main():
    """Run the MCP server."""
    logger.info("Starting acitrack MCP server...")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
