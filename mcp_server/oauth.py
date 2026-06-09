"""Minimal OAuth 2.1 + PKCE shim for claude.ai MCP custom connectors.

claude.ai's "Add custom connector" UI only accepts OAuth (no static bearer
header field), so we implement the smallest spec-compliant surface that makes
its OAuth client happy:

- RFC 9728 protected resource metadata (``/.well-known/oauth-protected-resource``)
- RFC 8414 authorization server metadata (``/.well-known/oauth-authorization-server``)
- RFC 7591 dynamic client registration (``/register``)
- ``/authorize`` with a one-click consent page (knowing the URL + clicking
  Approve is the trust signal — we're single-tenant)
- ``/token`` exchange with PKCE S256 verification
- 30-day access tokens, in-memory only (single uvicorn worker)

State is in-process memory. Restarting the service invalidates tokens — claude.ai
will silently re-run the flow next time Camille uses the connector.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

logger = logging.getLogger(__name__)


# --- Config -------------------------------------------------------------------
#
# Environment flags (read at request time, alongside SCIENCE_MCP_TOKEN /
# SCIENCE_MCP_PUBLIC_PREFIX below):
#
# MCP_ALLOW_DYNAMIC_REGISTRATION ("1"/"true"/"yes" to enable; default OFF):
#   RFC 7591 dynamic client registration is an open token mint — anyone who
#   can reach /register can mint a client_id and then walk the self-approvable
#   consent flow. Keep it OFF in production. Flip it on temporarily only while
#   (re)connecting a new MCP client (e.g. claude.ai re-registering after a
#   server restart wipes the in-memory client store), then turn it back off.
#   Already-issued tokens keep working while the flag is off.


def _dynamic_registration_enabled() -> bool:
    value = os.environ.get("MCP_ALLOW_DYNAMIC_REGISTRATION", "")
    return value.strip().lower() in ("1", "true", "yes")


# --- State ------------------------------------------------------------------


CODE_TTL_SECONDS = 10 * 60
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30


@dataclass
class _Client:
    client_id: str
    redirect_uris: list[str]
    client_name: str
    registered_at: float = field(default_factory=time.time)


@dataclass
class _AuthCode:
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scope: str
    issued_at: float = field(default_factory=time.time)


@dataclass
class _AccessToken:
    client_id: str
    scope: str
    issued_at: float = field(default_factory=time.time)


class OAuthStore:
    def __init__(self) -> None:
        self.clients: dict[str, _Client] = {}
        self.codes: dict[str, _AuthCode] = {}
        self.tokens: dict[str, _AccessToken] = {}

    def register(self, redirect_uris: list[str], client_name: str) -> _Client:
        cid = "cli_" + secrets.token_urlsafe(16)
        client = _Client(
            client_id=cid,
            redirect_uris=list(redirect_uris),
            client_name=client_name,
        )
        self.clients[cid] = client
        logger.info("oauth: registered client %s (%s)", cid, client_name)
        return client

    def issue_code(
        self,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
        scope: str,
    ) -> str:
        code = secrets.token_urlsafe(24)
        self.codes[code] = _AuthCode(
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            scope=scope,
        )
        return code

    def consume_code(self, code: str) -> Optional[_AuthCode]:
        rec = self.codes.pop(code, None)
        if rec and time.time() - rec.issued_at <= CODE_TTL_SECONDS:
            return rec
        return None

    def issue_token(self, client_id: str, scope: str) -> str:
        tok = "tok_" + secrets.token_urlsafe(32)
        self.tokens[tok] = _AccessToken(client_id=client_id, scope=scope)
        return tok

    def validate_token(self, tok: str) -> bool:
        rec = self.tokens.get(tok)
        if not rec:
            return False
        if time.time() - rec.issued_at > TOKEN_TTL_SECONDS:
            self.tokens.pop(tok, None)
            return False
        return True


_store = OAuthStore()


def store() -> OAuthStore:
    return _store


# --- Rate limiting ------------------------------------------------------------

# Simple in-memory sliding-window limiter for the OAuth surface. Dependency-free
# and per-process (single uvicorn worker, same as the token store). Keyed by
# (endpoint, client IP) so one noisy IP can't lock out the whole flow.

RATE_LIMIT_MAX_REQUESTS = 10
RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_BUCKETS_MAX_KEYS = 10_000

_rate_buckets: dict[tuple[str, str], list[float]] = {}


def _client_ip(request: Request) -> str:
    """Best-effort client IP: first X-Forwarded-For hop, else remote addr."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


def _rate_limited(request: Request, endpoint: str) -> Optional[JSONResponse]:
    """Return a 429 response if this IP exceeded the window, else record the hit."""
    now = time.time()

    # Opportunistic pruning so the bucket map can't grow without bound.
    if len(_rate_buckets) > _RATE_BUCKETS_MAX_KEYS:
        for key in [
            k for k, hits in _rate_buckets.items()
            if not hits or now - hits[-1] >= RATE_LIMIT_WINDOW_SECONDS
        ]:
            _rate_buckets.pop(key, None)

    key = (endpoint, _client_ip(request))
    window = [t for t in _rate_buckets.get(key, []) if now - t < RATE_LIMIT_WINDOW_SECONDS]
    if len(window) >= RATE_LIMIT_MAX_REQUESTS:
        _rate_buckets[key] = window
        logger.warning("oauth: rate limit exceeded for %s on %s", key[1], endpoint)
        return JSONResponse(
            {
                "error": "rate_limited",
                "error_description": "Too many requests; retry later.",
            },
            status_code=429,
            headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
        )
    window.append(now)
    _rate_buckets[key] = window
    return None


# --- URL helpers ------------------------------------------------------------


def _public_base(request: Request) -> str:
    """Return the public base URL the world sees, e.g.
    ``https://ai.spotitearly.com/science-mcp``.

    Respects ``X-Forwarded-Proto/Host/Prefix`` so URLs in metadata are correct
    when we sit behind nginx subpath proxying.
    """
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    prefix = request.headers.get("x-forwarded-prefix") or os.environ.get("SCIENCE_MCP_PUBLIC_PREFIX", "")
    return f"{proto}://{host}{prefix}".rstrip("/")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _state_secret() -> str:
    # Reuse the static admin token as the HMAC key for state envelopes.
    return os.environ.get("SCIENCE_MCP_TOKEN", "")


def _sign_state(payload: str) -> str:
    secret = _state_secret().encode() or b"unset"
    sig = hashlib.sha256(secret + payload.encode()).hexdigest()[:24]
    return f"{_b64url(payload.encode())}.{sig}"


def _unsign_state(token: str) -> Optional[str]:
    try:
        b64, sig = token.split(".", 1)
        payload = base64.urlsafe_b64decode(b64 + "==").decode()
    except Exception:
        return None
    secret = _state_secret().encode() or b"unset"
    expected = hashlib.sha256(secret + payload.encode()).hexdigest()[:24]
    if not secrets.compare_digest(expected, sig):
        return None
    return payload


# --- Endpoint handlers ------------------------------------------------------


async def protected_resource_metadata(request: Request) -> JSONResponse:
    base = _public_base(request)
    return JSONResponse({
        "resource": f"{base}/mcp/",
        "authorization_servers": [base],
        "scopes_supported": ["mcp"],
        "bearer_methods_supported": ["header"],
    })


async def authorization_server_metadata(request: Request) -> JSONResponse:
    base = _public_base(request)
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "registration_endpoint": f"{base}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["mcp"],
    })


async def register(request: Request) -> JSONResponse:
    limited = _rate_limited(request, "register")
    if limited:
        return limited

    if not _dynamic_registration_enabled():
        logger.warning("oauth: dynamic registration attempt rejected (flag off)")
        return JSONResponse(
            {
                "error": "access_denied",
                "error_description": (
                    "Dynamic client registration is disabled on this server. "
                    "Set MCP_ALLOW_DYNAMIC_REGISTRATION=1 to allow new MCP "
                    "clients to register."
                ),
            },
            status_code=403,
        )

    try:
        body = await request.json()
    except Exception:
        body = {}

    redirect_uris = body.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return JSONResponse(
            {"error": "invalid_redirect_uri", "error_description": "redirect_uris required"},
            status_code=400,
        )

    client_name = (body.get("client_name") or "MCP client")[:80]
    client = _store.register(redirect_uris=redirect_uris, client_name=client_name)
    base = _public_base(request)
    return JSONResponse(
        {
            "client_id": client.client_id,
            "client_id_issued_at": int(client.registered_at),
            "redirect_uris": client.redirect_uris,
            "client_name": client.client_name,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "registration_client_uri": f"{base}/register",
        },
        status_code=201,
    )


_APPROVE_HTML = """\
<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Science Agent — approve access</title>
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 520px;
         margin: 4em auto; padding: 1.5em; background: #fafafa; color: #1a1a1a;
         line-height: 1.5 }}
  .card {{ background: #fff; border: 1px solid #e2e2e2; border-radius: 10px;
           padding: 1.75em; box-shadow: 0 1px 3px rgba(0,0,0,.04) }}
  h1 {{ font-size: 1.3em; margin: 0 0 .8em }}
  .meta {{ font-family: ui-monospace, SFMono-Regular, monospace; font-size: .85em;
           color: #666; word-break: break-all; background: #f5f5f5;
           padding: .5em .7em; border-radius: 6px }}
  button {{ font: inherit; padding: .65em 1.3em; border: 0; border-radius: 6px;
            cursor: pointer; font-weight: 500 }}
  .approve {{ background: #2563eb; color: #fff }}
  .approve:hover {{ background: #1d4ed8 }}
  .deny {{ background: #f1f1f1; color: #333; margin-left: .5em }}
  small {{ color: #777 }}
</style></head><body>
<div class="card">
<h1>Approve access to Science Agent</h1>
<p><strong>{client_name}</strong> is requesting read-only access to the
SpotitEarly Science Agent corpus (publication search and lookup).</p>
<p class="meta">redirects to: {redirect_uri}</p>
<form method="POST" action="{post_url}">
  <input type="hidden" name="state_token" value="{state_token}">
  <button class="approve" type="submit" name="approved" value="yes">Approve</button>
  <button class="deny"    type="submit" name="approved" value="no">Deny</button>
</form>
<p><small>Tokens last 30 days. You only see this page when first connecting a new client.</small></p>
</div></body></html>
"""


async def authorize(request: Request) -> Response:
    limited = _rate_limited(request, "authorize")
    if limited:
        return limited

    q = request.query_params
    client_id = q.get("client_id", "")
    redirect_uri = q.get("redirect_uri", "")
    code_challenge = q.get("code_challenge", "")
    code_challenge_method = q.get("code_challenge_method", "S256")
    state = q.get("state", "")
    scope = q.get("scope", "mcp")
    response_type = q.get("response_type", "")

    if response_type != "code":
        return JSONResponse(
            {"error": "unsupported_response_type"}, status_code=400
        )

    client = _store.clients.get(client_id)
    if not client:
        return JSONResponse(
            {"error": "invalid_client", "error_description": "client_id not registered"},
            status_code=400,
        )
    if redirect_uri not in client.redirect_uris:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "redirect_uri not registered"},
            status_code=400,
        )
    if code_challenge_method != "S256" or not code_challenge:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "PKCE S256 required"},
            status_code=400,
        )

    state_payload = json.dumps({
        "cid": client_id,
        "ru": redirect_uri,
        "cc": code_challenge,
        "ccm": code_challenge_method,
        "sc": scope,
        "st": state,
        "ts": int(time.time()),
    })
    state_token = _sign_state(state_payload)
    base = _public_base(request)

    return HTMLResponse(_APPROVE_HTML.format(
        client_name=client.client_name,
        redirect_uri=redirect_uri,
        post_url=f"{base}/authorize/approve",
        state_token=state_token,
    ))


def _redirect_with(uri: str, params: dict[str, str]) -> RedirectResponse:
    sep = "&" if "?" in uri else "?"
    return RedirectResponse(url=f"{uri}{sep}{urlencode(params)}", status_code=302)


async def approve(request: Request) -> Response:
    limited = _rate_limited(request, "approve")
    if limited:
        return limited

    form = await request.form()
    state_token = form.get("state_token", "")
    approved = form.get("approved", "")
    payload = _unsign_state(state_token)
    if not payload:
        return JSONResponse({"error": "invalid_state"}, status_code=400)
    p = json.loads(payload)

    if time.time() - p["ts"] > 15 * 60:
        return JSONResponse({"error": "expired_state"}, status_code=400)

    if approved != "yes":
        return _redirect_with(p["ru"], {"error": "access_denied", "state": p["st"]})

    code = _store.issue_code(
        client_id=p["cid"],
        redirect_uri=p["ru"],
        code_challenge=p["cc"],
        code_challenge_method=p["ccm"],
        scope=p["sc"],
    )
    return _redirect_with(p["ru"], {"code": code, "state": p["st"]})


async def token(request: Request) -> JSONResponse:
    limited = _rate_limited(request, "token")
    if limited:
        return limited

    form = await request.form()
    grant_type = form.get("grant_type", "")

    if grant_type == "authorization_code":
        code = form.get("code", "")
        verifier = form.get("code_verifier", "")
        client_id = form.get("client_id", "")
        rec = _store.consume_code(code)
        if not rec or rec.client_id != client_id:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        expected = _b64url(hashlib.sha256(verifier.encode()).digest())
        if not secrets.compare_digest(expected, rec.code_challenge):
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "PKCE verification failed"},
                status_code=400,
            )
        access_token = _store.issue_token(client_id, rec.scope)
        return JSONResponse({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": TOKEN_TTL_SECONDS,
            "refresh_token": access_token,
            "scope": rec.scope,
        })

    if grant_type == "refresh_token":
        rt = form.get("refresh_token", "")
        if not _store.validate_token(rt):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        old = _store.tokens[rt]
        new = _store.issue_token(old.client_id, old.scope)
        return JSONResponse({
            "access_token": new,
            "token_type": "Bearer",
            "expires_in": TOKEN_TTL_SECONDS,
            "refresh_token": new,
            "scope": old.scope,
        })

    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
