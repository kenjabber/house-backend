"""The /mcp endpoint end to end, and the REST caller (PLAN D-14's /mcp line:
a bearer token only, never the session cookie; POST only; Host and Origin
checked against the RP ID, the localhost names and MCP_ALLOWED_HOSTS)."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

from house_backend.mcp.catalog import Catalog
from house_backend.mcp.rest import RestCaller
from house_backend.testing import McpClient
from tests.apps import multi
from tests.apps.multi import Team


def test_a_tool_call_with_a_token(mcp, session):
    session.add(Team(name="Blue"))
    session.commit()
    assert mcp.call("list_teams") == {"teams": [{"id": 1, "name": "Team"}, {"id": 2, "name": "Blue"}]}
    assert list(mcp.tools()) == ["list_teams"]


def test_results_pass_through_the_redaction_hook(mcp, session):
    session.add(Team(name="secret"))
    session.commit()
    assert {"id": 2, "name": "<redacted>"} in mcp.call("list_teams")["teams"]


def test_mcp_requires_an_api_token(client):
    response = McpClient(client, None).post("tools/list")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="multi"'
    assert response.json()["detail"] == "MCP requires an API token (Settings → Tokens)"


def test_mcp_refuses_a_wrong_token(client, api_token):
    assert McpClient(client, "multi_not-real").post("tools/list").status_code == 401


def test_mcp_does_not_accept_the_browser_session(auth_client):
    assert McpClient(auth_client, None).post("tools/list").status_code == 401


def test_mcp_only_accepts_post(client, api_token):
    for method in ("GET", "DELETE"):
        r = client.request(method, "/mcp", headers={"authorization": f"Bearer {api_token}", "host": "localhost"})
        assert r.status_code == 405 and r.headers["allow"] == "POST"


def test_mcp_refuses_an_unknown_host_and_origin(mcp):
    assert mcp.post("tools/list", headers={"host": "rebound.example.test"}).status_code == 421
    assert mcp.post("tools/list", headers={"origin": "http://evil.example.test"}).status_code == 403
    assert mcp.post("tools/list", headers={"host": "localhost:8000"}).status_code == 200
    assert mcp.post("tools/list", headers={"origin": "http://localhost:5998"}).status_code == 200


def test_mcp_allowed_hosts_widen_the_list(engine, monkeypatch, house_auth, api_token):
    monkeypatch.setattr(house_auth.settings, "MCP_ALLOWED_HOSTS", "app.example.test")
    with TestClient(multi.app) as c:  # the allow-list is built at start-up
        mcp = McpClient(c, api_token)
        assert mcp.post("tools/list", headers={"host": "app.example.test"}).status_code == 200
        assert mcp.post("tools/list", headers={"host": "other.example.test"}).status_code == 421


def test_mcp_is_open_only_under_the_dev_auth_bypass(client, monkeypatch, house_auth):
    monkeypatch.setattr(house_auth.settings, "AUTH_DISABLED", True)
    assert McpClient(client, None).post("tools/list").status_code == 200
    assert McpClient(client, "multi_wrong").post("tools/list").status_code == 401


def test_a_members_token_reads_but_cannot_write(client, session, member, house_auth):
    _row, token = house_auth.tokens.create_token(session, user_id=member.id, name="m")
    assert McpClient(client, token).call("list_teams")["teams"][0]["name"] == "Team"
    # The REST layer is the guard: a write the member may not make fails there.
    caller = RestCaller(house_auth, Catalog({"t": (("POST", "/api/teams"),)}, {}, {}))
    with pytest.raises(ToolError, match="403 POST /api/teams: admin only"):
        asyncio.run(caller.call(multi.app, f"Bearer {token}", "POST", "/api/teams", body={"name": "x"}))


def test_rest_refuses_unexposed_operations():
    with pytest.raises(RuntimeError, match="not an exposed MCP operation"):
        asyncio.run(multi.rest.call(multi.app, None, "GET", "/api/auth/tokens"))


def test_rest_marks_its_calls_as_an_agents(client, auth_client, house_auth):
    """Even with a session cookie, a call through the MCP layer is refused the
    human-only routes: the via header marks it."""
    caller = RestCaller(house_auth, Catalog({"t": (("GET", "/api/auth/sessions"),)}, {}, {}))
    sid = auth_client.cookies.get(house_auth.SESSION_COOKIE_NAME)

    async def call():
        import httpx

        transport = httpx.ASGITransport(app=multi.app)
        cookies = {house_auth.SESSION_COOKIE_NAME: sid}
        async with httpx.AsyncClient(transport=transport, base_url=caller.base_url, cookies=cookies) as c:
            plain = await c.get("/api/auth/sessions")
            marked = await c.get("/api/auth/sessions", headers={house_auth.VIA_HEADER: house_auth.VIA_MCP})
        return plain.status_code, marked.status_code

    assert asyncio.run(call()) == (200, 403)


def test_rest_fills_the_path_and_query_and_returns_raw_text(client, api_token, house_auth):
    caller = RestCaller(house_auth, Catalog({"t": (("GET", "/api/status"),)}, {}, {}))
    out = asyncio.run(caller.call(multi.app, f"Bearer {api_token}", "GET", "/api/status", query={"x": True}, raw=True))
    assert out == '{"status":"ok"}'
