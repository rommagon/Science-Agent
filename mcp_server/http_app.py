"""Streamable-HTTP MCP transport for remote claude.ai connectors.

Exposes the shared :mod:`mcp_server.registry` tools over HTTPS so a Claude.ai
Project can add this as a custom connector. Two auth flavors are accepted:

* The static ``SCIENCE_MCP_TOKEN`` bearer (handy for curl / scripts / tests).
* OAuth 2.1 + PKCE access tokens issued by :mod:`mcp_server.oauth`
  (claude.ai's connector UI only supports OAuth).

The OAuth shim self-advertises via RFC 9728 metadata: a 401 response includes
``WWW-Authenticate: Bearer resource_metadata="..."`` and claude.ai discovers
the auth server from there.

Run locally::

    SCIENCE_MCP_TOKEN=dev DATABASE_URL=postgresql://... \\
        uvicorn mcp_server.http_app:app --port 5006 --workers 1

Behind nginx, set ``X-Forwarded-Proto/Host/Prefix`` so OAuth metadata returns
the public URLs (the production nginx config sends them).
"""

from __future__ import annotations

import contextlib
import hmac
import logging
import os
from typing import Any

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

from mcp_server import oauth
from mcp_server.registry import TOOLS, dispatch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# --- MCP server (shared registry) -------------------------------------------

mcp_app = Server("acitrack-mcp-http")


@mcp_app.list_tools()
async def _list_tools() -> list[Tool]:
    return TOOLS


@mcp_app.call_tool()
async def _call_tool(name: str, arguments: Any) -> list[TextContent]:
    return await dispatch(name, arguments)


session_manager = StreamableHTTPSessionManager(
    app=mcp_app,
    event_store=None,
    json_response=False,
    stateless=True,
)


async def _mcp_asgi(scope: Scope, receive: Receive, send: Send) -> None:
    # Mount("/mcp", ...) strips "/mcp" from the path before invoking us, so
    # what we receive is "" (no slash) or "/" (with slash) or "/foo".
    # The streamable-HTTP manager 307-redirects empty/missing paths; rewrite
    # to "/" so it handles both /mcp and /mcp/ uniformly without redirecting.
    if scope.get("type") == "http" and scope.get("path", "") in ("", "/"):
        scope = {**scope, "path": "/", "raw_path": b"/"}
    await session_manager.handle_request(scope, receive, send)


# --- Auth middleware --------------------------------------------------------

# Paths the middleware leaves unauthenticated. OAuth discovery + dance lives
# here, plus the health probe.
_PUBLIC_PATHS = frozenset({
    "/healthz",
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
    "/.well-known/oauth-authorization-server",
    "/.well-known/openid-configuration",
    "/register",
    "/authorize",
    "/authorize/approve",
    "/token",
})


def _public_resource_metadata_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    prefix = request.headers.get("x-forwarded-prefix") or os.environ.get("SCIENCE_MCP_PUBLIC_PREFIX", "")
    return f"{proto}://{host}{prefix}/.well-known/oauth-protected-resource"


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Accept either the static admin token or an OAuth-issued access token.

    On rejection, emits ``WWW-Authenticate`` per RFC 6750 + RFC 9728 so the
    MCP client can discover our OAuth metadata and start the flow.
    """

    def __init__(self, app, static_token: str | None) -> None:
        super().__init__(app)
        self._static_token = static_token or ""
        if not self._static_token:
            logger.warning(
                "SCIENCE_MCP_TOKEN is empty — only OAuth-issued tokens will be accepted"
            )

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        path = request.url.path
        if path in _PUBLIC_PATHS:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            presented = header[len("bearer "):].strip()
            if self._static_token and hmac.compare_digest(presented, self._static_token):
                return await call_next(request)
            if oauth.store().validate_token(presented):
                return await call_next(request)

        challenge = (
            'Bearer realm="science-mcp", '
            f'resource_metadata="{_public_resource_metadata_url(request)}"'
        )
        return JSONResponse(
            {"error": "unauthorized"},
            status_code=401,
            headers={"WWW-Authenticate": challenge},
        )


# --- App assembly -----------------------------------------------------------


async def _healthz(_request: Request) -> Response:
    return JSONResponse({"status": "ok"})


@contextlib.asynccontextmanager
async def _lifespan(_app: Starlette):
    async with session_manager.run():
        logger.info("Science MCP HTTP server ready (streamable-http, stateless)")
        yield


def create_app() -> Starlette:
    static_token = os.environ.get("SCIENCE_MCP_TOKEN", "")
    middleware = [Middleware(BearerAuthMiddleware, static_token=static_token)]

    routes = [
        Route("/healthz", _healthz, methods=["GET"]),
        # OAuth discovery metadata (RFC 9728 / 8414). Both paths supported for
        # client-side variation in where they look.
        Route("/.well-known/oauth-protected-resource", oauth.protected_resource_metadata, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource/mcp", oauth.protected_resource_metadata, methods=["GET"]),
        Route("/.well-known/oauth-authorization-server", oauth.authorization_server_metadata, methods=["GET"]),
        # Some MCP clients (incl. claude.ai) probe the OIDC discovery URL
        # instead of the OAuth one. Same metadata is OIDC-compatible.
        Route("/.well-known/openid-configuration", oauth.authorization_server_metadata, methods=["GET"]),
        # OAuth flow.
        Route("/register", oauth.register, methods=["POST"]),
        Route("/authorize", oauth.authorize, methods=["GET"]),
        Route("/authorize/approve", oauth.approve, methods=["POST"]),
        Route("/token", oauth.token, methods=["POST"]),
        # The MCP transport itself.
        Mount("/mcp", app=_mcp_asgi),
    ]

    starlette_app = Starlette(
        debug=False,
        routes=routes,
        middleware=middleware,
        lifespan=_lifespan,
    )
    # claude.ai POSTs to /mcp without a trailing slash; the default 307
    # redirect to /mcp/ breaks the MCP transport (clients drop body+auth
    # on a redirect). Disable the redirect AND rewrite the slashless form
    # before routing so Mount matches it directly.
    starlette_app.router.redirect_slashes = False
    return _MCPPathRewriter(starlette_app)


class _MCPPathRewriter:
    """ASGI shim that rewrites ``/mcp`` to ``/mcp/`` before routing.

    With ``redirect_slashes=False``, ``Mount("/mcp", ...)`` doesn't match
    the slashless form. Rewriting the path here lets one Mount serve both.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http" and scope.get("path") == "/mcp":
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}
        await self.app(scope, receive, send)


app = create_app()
