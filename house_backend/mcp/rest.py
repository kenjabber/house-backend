"""The in-process REST caller the MCP tools use.

Tools never touch the database or services directly: they call the app's own
REST API through an ASGI transport (no socket, same process), so every
request runs the real router code (auth, validation, checks, audit rows).

Enforced here rather than left to each tool:

* only operations the catalog exposes can be called;
* each call is marked as an agent's (the via header) and carries the
  caller's own bearer token, so ``require_human`` refuses the hold-backs
  however a tool is written;
* each result passes through the app's redaction hook, if it has one.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.mcpserver.exceptions import ToolError
from starlette.types import ASGIApp

from house_backend.auth import HouseAuth
from house_backend.mcp.catalog import Catalog

# ``redact(method, template, data) -> data``: ``data`` is the parsed JSON, or
# the text when the call was ``raw``.
RedactHook = Callable[[str, str, Any], Any]


def error_detail(response: httpx.Response) -> str:
    """A readable message from a FastAPI error response."""
    try:
        body = response.json()
    except ValueError:
        return response.text or response.reason_phrase
    detail = body.get("detail", body) if isinstance(body, dict) else body
    return detail if isinstance(detail, str) else json.dumps(detail)


class RestCaller:
    def __init__(self, auth: HouseAuth, catalog: Catalog, redact: RedactHook | None = None):
        self.auth = auth
        self.catalog = catalog
        self.redact = redact
        self.base_url = f"http://{auth.app_name}.internal"

    async def call(
        self,
        app: ASGIApp,
        authorization: str | None,
        method: str,
        template: str,
        *,
        path: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: Any = None,
        raw: bool = False,
    ) -> Any:
        """Call one REST operation; returns the parsed JSON (the text when ``raw``).

        ``template`` is the OpenAPI path template (``/api/items/{item_id}``);
        ``path`` fills its parameters. A 4xx or 5xx becomes a ``ToolError``
        carrying the API's own ``detail``, so the model sees why it failed."""
        if (method, template) not in self.catalog.exposed_operations():
            raise RuntimeError(f"{method} {template} is not an exposed MCP operation")

        url = template.format(**{name: quote(str(value), safe="") for name, value in (path or {}).items()})
        headers = {self.auth.VIA_HEADER: self.auth.VIA_MCP}
        if authorization:
            headers["authorization"] = authorization
        params = {k: str(v).lower() if isinstance(v, bool) else v for k, v in (query or {}).items() if v is not None}

        # No timeout: the transport is in-process, and some operations are slow.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=self.base_url, timeout=None
        ) as client:
            response = await client.request(method, url, params=params, json=body, headers=headers)

        if response.status_code >= 400:
            raise ToolError(f"{response.status_code} {method} {url}: {error_detail(response)}")
        if raw:
            data: Any = response.text
        elif response.status_code == 204 or not response.content:
            return None
        else:
            data = response.json()
        return self.redact(method, template, data) if self.redact else data
