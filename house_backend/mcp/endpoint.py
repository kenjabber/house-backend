"""Mounting the MCP server inside the FastAPI app.

``/mcp`` speaks MCP's Streamable HTTP transport, stateless, with JSON
responses: a plain request/response endpoint behind the same proxies as
``/api``.

Authentication happens here, before any MCP handling: a valid API token
(``Authorization: Bearer …``), or, under the development-only
``AUTH_DISABLED``, nothing. The session cookie is deliberately *not*
accepted: a browser must never be able to drive the agent endpoint, so a
cross-site request cannot ride a signed-in user's session.

The SDK's session manager can be entered only once, so a fresh server is
built per app start-up (:func:`mcp_lifespan`) and the route
(:class:`McpEndpoint`) looks the live handler up on ``app.state`` at request
time. Tests that start the app several times depend on it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from anyio import to_thread
from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from house_backend.auth import HouseAuth
from house_backend.settings import HouseSettings

MCP_PATH = "/mcp"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def transport_security(settings: HouseSettings) -> TransportSecuritySettings:
    """The Host/Origin allow-list (DNS-rebinding protection): the WebAuthn RP
    ID, the localhost names and ``MCP_ALLOWED_HOSTS``, any port; origins are
    the WebAuthn origins and localhost.

    It matters most in development, where ``AUTH_DISABLED`` leaves ``/mcp``
    open on localhost: a web page that rebinds its name to 127.0.0.1 is
    refused because its Host header is not in the list."""
    hosts = [settings.WEBAUTHN_RP_ID, *LOCAL_HOSTS, *settings.mcp_extra_hosts]
    allowed_hosts = [h for host in dict.fromkeys(hosts) for h in (host, f"{host}:*")]
    allowed_origins = [*settings.webauthn_allowed_origins, *(f"http://{host}:*" for host in LOCAL_HOSTS)]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


@asynccontextmanager
async def mcp_lifespan(app: FastAPI, build_server: Callable[[], Any], settings: HouseSettings) -> AsyncIterator[None]:
    """Run the MCP server ``build_server()`` returns for the lifetime of the app."""
    server = build_server()
    starlette_app = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        transport_security=transport_security(settings),
    )
    # The SDK's Starlette app is one Route wrapping the ASGI handler; take the
    # handler so it is served from this app's own /mcp route.
    app.state.mcp_handler = starlette_app.routes[0].app
    async with server.session_manager.run():
        try:
            yield
        finally:
            app.state.mcp_handler = None


class McpEndpoint:
    """The ``/mcp`` ASGI endpoint: authenticate, then hand over to the SDK.

    ``unauthorized_detail`` is the 401 text, which tells a person where the
    app's API tokens are made."""

    def __init__(self, auth: HouseAuth, unauthorized_detail: str = "MCP requires an API token"):
        if not auth.api_tokens:
            raise ValueError("/mcp needs API tokens (HouseAuth(api_tokens=True))")
        self.auth = auth
        self.unauthorized_detail = unauthorized_detail

    def _token_is_valid(self, app: FastAPI, token: str) -> bool:
        session = app.state.SessionFactory()
        try:
            return self.auth.tokens.user_for_token(session, token) is not None
        finally:
            session.close()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope)
        app: FastAPI = scope["app"]
        handler = getattr(app.state, "mcp_handler", None)
        if handler is None:
            await JSONResponse({"detail": "MCP server not running"}, status_code=503)(scope, receive, send)
            return

        token = self.auth.bearer_token(request)
        if token is not None:
            authorized = await to_thread.run_sync(self._token_is_valid, app, token)
        else:
            authorized = self.auth.settings.AUTH_DISABLED
        if not authorized:
            await JSONResponse(
                {"detail": self.unauthorized_detail},
                status_code=401,
                headers={"www-authenticate": f'Bearer realm="{self.auth.app_name}"'},
            )(scope, receive, send)
            return

        # Stateless with JSON responses: the server never starts a message, so
        # there is no SSE stream to open (GET) and no session to end (DELETE).
        # The spec's answer for both is 405, which also keeps idle streams from
        # lingering behind the proxies.
        if request.method != "POST":
            await JSONResponse(
                {"detail": "this MCP endpoint only accepts POST"},
                status_code=405,
                headers={"allow": "POST"},
            )(scope, receive, send)
            return

        await handler(scope, receive, send)
