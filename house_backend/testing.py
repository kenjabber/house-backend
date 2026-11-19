"""pytest fixtures and helpers for an app built on house-backend (PLAN D-17).

In the app's ``tests/conftest.py``::

    pytest_plugins = ["house_backend.testing"]

    @pytest.fixture
    def house_app():  return main.app
    @pytest.fixture
    def house_auth(): return dependencies.auth.auth
    @pytest.fixture
    def house_base(): return schema.Base

and, when they differ from the defaults, ``house_db_setting`` (the settings
attribute holding the database path, default ``SQLITE_PATH``) and
``house_user_values`` (the app's own column values for a seeded user).

The fixtures: ``engine``, ``session``, ``client``, ``seed_user``, ``login_as``,
``admin``, ``member``, ``auth_client``, ``member_client``, ``api_token``,
``mcp`` (an :class:`McpClient`) and ``fake_passkeys`` (a :class:`FakePasskeys`).
:func:`seed_user` and :func:`login_as` are also plain functions, for an
app's own helpers (``seed_user(session, …)`` kept with its old signature).
``AUTH_DISABLED`` is pinned off for every test, since a developer's ``.env``
may set it. The MCP parity checks are functions an app's test calls:
:func:`check_operations_partition` and :func:`check_human_only_refuses_tokens`.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from house_backend.auth import HouseAuth
from house_backend.db import create_sqlite_engine

# ── What the app supplies ────────────────────────────────────────────────────


@pytest.fixture
def house_app() -> Any:
    """The app's FastAPI object. Override in the app's conftest."""
    raise pytest.UsageError("define a `house_app` fixture returning the FastAPI app")


@pytest.fixture
def house_auth() -> HouseAuth | None:
    """The app's HouseAuth. Override in the app's conftest."""
    return None


@pytest.fixture
def house_base() -> Any:
    """The app's declarative ``Base``. Override in the app's conftest."""
    raise pytest.UsageError("define a `house_base` fixture returning the declarative Base")


@pytest.fixture
def house_db_setting() -> str:
    """The settings attribute that holds the database path."""
    return "SQLITE_PATH"


@pytest.fixture
def house_user_values() -> dict[str, Any]:
    """The app's own column values for a seeded user (a team, a household)."""
    return {}


# ── Database and client ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _house_auth_enabled(house_auth, monkeypatch) -> None:
    """Never let a developer's ``.env`` switch auth off under test."""
    if house_auth is not None:
        monkeypatch.setattr(house_auth.settings, "AUTH_DISABLED", False)
        house_auth.rate_limiter.reset()


@pytest.fixture
def engine(tmp_path, monkeypatch, house_auth, house_base, house_db_setting) -> Iterator[Engine]:
    """A throw-away database; the app's lifespan opens the same file."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(house_auth.settings, house_db_setting, str(path))
    eng = create_sqlite_engine(path)
    house_base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    sess = factory()
    try:
        yield sess
    finally:
        sess.close()


@pytest.fixture
def client(engine, house_app) -> Iterator[TestClient]:
    """A TestClient with the app's lifespan run against the throw-away database.

    ``app.state.SessionFactory`` is set on the test database first, so
    ``house_app`` may also be an app of the routers alone, without the real
    lifespan (an app whose start-up runs background work the tests must not
    start); a lifespan that sets its own replaces it, on the same file."""
    _use_test_database(house_app, engine)
    with TestClient(house_app) as c:
        yield c


def _use_test_database(app: Any, engine: Engine) -> None:
    app.state.SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# ── Users and sign-in ────────────────────────────────────────────────────────


def seed_user(
    auth: HouseAuth,
    session: Session,
    user_id: str = "test-admin",
    username: str = "alex",
    is_admin: bool = True,
    **values: Any,
) -> Any:
    """A user row with ``values`` as the app's own columns (returned as it
    is if the id exists). For an app's own helpers; tests use the fixture."""
    model = auth.models.User
    user = session.get(model, user_id)
    if user is None:
        user = model(id=user_id, username=username, display_name=username.capitalize(), is_admin=is_admin, **values)
        session.add(user)
        session.commit()
    return user


def login_as(auth: HouseAuth, client: TestClient, session: Session, user: Any) -> TestClient:
    """Give ``client`` a session cookie for ``user`` (a session row, a day long)."""
    sid = f"session-{user.id}"
    model = auth.models.UserSession
    if session.get(model, sid) is None:
        expires = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1)
        session.add(model(session_id=sid, user_id=user.id, expires_at=expires))
        session.commit()
    client.cookies.set(auth.SESSION_COOKIE_NAME, sid)
    return client


@pytest.fixture(name="seed_user")
def _seed_user_fixture(session, house_auth, house_user_values) -> Callable[..., Any]:
    """``seed_user(user_id="u1", username="alex", is_admin=True, **columns)``:
    :func:`seed_user` for this app, with ``house_user_values`` filled in."""

    def seed(user_id: str = "test-admin", username: str = "alex", is_admin: bool = True, **values: Any) -> Any:
        return seed_user(house_auth, session, user_id, username, is_admin, **{**house_user_values, **values})

    return seed


@pytest.fixture(name="login_as")
def _login_as_fixture(session, house_auth) -> Callable[[TestClient, Any], TestClient]:
    """``login_as(client, user)``: :func:`login_as` for this app."""

    def login(client: TestClient, user: Any) -> TestClient:
        return login_as(house_auth, client, session, user)

    return login


@pytest.fixture
def admin(seed_user) -> Any:
    return seed_user()


@pytest.fixture
def member(seed_user) -> Any:
    return seed_user(user_id="test-member", username="sam", is_admin=False)


@pytest.fixture
def auth_client(client, admin, login_as) -> TestClient:
    """The client, signed in as the seeded admin."""
    return login_as(client, admin)


@pytest.fixture
def member_client(engine, house_app, member, login_as) -> Iterator[TestClient]:
    """A second client, signed in as a non-admin member."""
    _use_test_database(house_app, engine)
    with TestClient(house_app) as c:
        yield login_as(c, member)


@pytest.fixture
def api_token(client, session, admin, house_auth) -> str:
    """The plaintext of an API token of the seeded admin."""
    _row, plaintext = house_auth.tokens.create_token(session, user_id=admin.id, name="test")
    return plaintext


# ── MCP ──────────────────────────────────────────────────────────────────────


class McpClient:
    """Speaks MCP's JSON-RPC to ``/mcp`` through a TestClient."""

    def __init__(self, client: TestClient, token: str | None):
        self._client = client
        self.token = token
        self._ids = 0

    def post(self, method: str, params: dict | None = None, *, headers: dict | None = None):
        self._ids += 1
        base = {"host": "localhost", "accept": "application/json, text/event-stream"}
        if self.token:
            base["authorization"] = f"Bearer {self.token}"
        base.update(headers or {})
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": self._ids, "method": method}
        if params is not None:
            body["params"] = params
        return self._client.post("/mcp", json=body, headers=base)

    def rpc(self, method: str, params: dict | None = None) -> dict:
        response = self.post(method, params)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert "error" not in payload, payload
        return payload["result"]

    def tools(self) -> dict[str, dict]:
        return {t["name"]: t for t in self.rpc("tools/list")["tools"]}

    def _call(self, tool: str, arguments: dict) -> dict:
        return self.rpc("tools/call", {"name": tool, "arguments": arguments})

    def call(self, _tool: str, **arguments: Any) -> Any:
        """A tool's parsed result (``_tool``, so a tool may itself take ``name``)."""
        result = self._call(_tool, arguments)
        text = result["content"][0]["text"]
        assert not result.get("isError"), text
        return json.loads(text)

    def call_error(self, _tool: str, **arguments: Any) -> str:
        result = self._call(_tool, arguments)
        assert result.get("isError"), result
        return result["content"][0]["text"]


@pytest.fixture
def mcp(client, api_token) -> McpClient:
    return McpClient(client, api_token)


# ── Faked passkey ceremonies ─────────────────────────────────────────────────


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class FakePasskeys:
    """Stands in for an authenticator: replaces the ``webauthn`` library's two
    verify functions, and builds the responses a browser would send for the
    options a ``begin`` route returned. The challenge round-trip, the stored
    credential, the sign counter and every check around them are real."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch):
        from house_backend.auth import service

        self.sign_counts: dict[str, int] = {}
        monkeypatch.setattr(service, "verify_registration_response", self._verify_registration)
        monkeypatch.setattr(service, "verify_authentication_response", self._verify_authentication)

    @staticmethod
    def _client_data(options: dict[str, Any], kind: str) -> str:
        data = {"type": kind, "challenge": options["challenge"], "origin": "http://localhost"}
        return _b64url(json.dumps(data).encode())

    @staticmethod
    def credential_id(name: str) -> str:
        """The (base64url) credential id of the fake passkey called ``name``."""
        return _b64url(name.encode())

    def registration(self, options: dict[str, Any], name: str = "passkey-1") -> dict[str, Any]:
        """The browser's answer to registration ``options`` (a new passkey)."""
        credential_id = self.credential_id(name)
        return {
            "id": credential_id,
            "rawId": credential_id,
            "type": "public-key",
            "response": {"clientDataJSON": self._client_data(options, "webauthn.create"), "transports": ["internal"]},
        }

    def assertion(self, options: dict[str, Any], name: str = "passkey-1") -> dict[str, Any]:
        """The browser's answer to sign-in ``options``, signed with the passkey ``name``."""
        credential_id = self.credential_id(name)
        return {
            "id": credential_id,
            "rawId": credential_id,
            "type": "public-key",
            "response": {"clientDataJSON": self._client_data(options, "webauthn.get")},
        }

    def _verify_registration(self, *, credential: dict[str, Any], **_: Any) -> Any:
        raw = credential["rawId"]
        padded = raw + "=" * (-len(raw) % 4)
        self.sign_counts[raw] = 0
        return SimpleNamespace(
            credential_id=base64.urlsafe_b64decode(padded),
            credential_public_key=b"fake-public-key",
            sign_count=0,
            aaguid="00000000-0000-0000-0000-000000000000",
        )

    def _verify_authentication(self, *, credential: dict[str, Any], **_: Any) -> Any:
        raw = credential["rawId"]
        self.sign_counts[raw] = self.sign_counts.get(raw, 0) + 1
        return SimpleNamespace(new_sign_count=self.sign_counts[raw])


@pytest.fixture
def fake_passkeys(monkeypatch) -> FakePasskeys:
    return FakePasskeys(monkeypatch)


# ── MCP parity checks ────────────────────────────────────────────────────────
# An app's tests/test_mcp_parity.py calls these with its own app and catalog.


def openapi_operations(app: Any) -> set[tuple[str, str]]:
    """Every (METHOD, path template) the app's OpenAPI document lists."""
    spec = app.openapi()
    return {(method.upper(), path) for path, ops in spec["paths"].items() for method in ops}


def check_operations_partition(app: Any, catalog: Any) -> None:
    """Every REST operation is exactly one of exposed, equivalent or
    human-only; the catalog names no operation the app no longer serves; and
    every equivalent names a real tool."""
    exposed, equivalent, human = catalog.exposed_operations(), set(catalog.equivalent), set(catalog.human_only)
    actual = openapi_operations(app)
    unclassified = sorted(actual - (exposed | equivalent | human))
    assert not unclassified, f"unclassified REST operations (classify them in the MCP catalog): {unclassified}"
    stale = sorted((exposed | equivalent | human) - actual)
    assert not stale, f"the catalog names operations the app no longer serves: {stale}"
    overlap = sorted((exposed & equivalent) | (exposed & human) | (equivalent & human))
    assert not overlap, f"operations classified twice: {overlap}"
    unknown = sorted(set(catalog.equivalent.values()) - set(catalog.tool_names()))
    assert not unknown, f"equivalents name tools that do not exist: {unknown}"
    assert catalog.human_only_enforced <= human, "human_only_enforced names operations not in human_only"


def check_tools_match_catalog(mcp: McpClient, catalog: Any) -> None:
    """The server registers exactly the catalog's tools, each documented and annotated."""
    tools = mcp.tools()
    assert sorted(tools) == sorted(catalog.tool_names())
    for name, tool in tools.items():
        assert tool.get("description", "").strip(), f"{name} has no description"
        assert "readOnlyHint" in (tool.get("annotations") or {}), f"{name} has no annotations"


def check_human_only_refuses_tokens(
    client: TestClient, token: str, catalog: Any, path_values: dict[str, str] | None = None
) -> None:
    """Every enforced human-only operation refuses an API token with 403: the
    hold-backs are enforced at the REST layer, not just left out of the tools.
    Path parameters are filled from ``path_values`` (default ``"1"``)."""
    import string

    headers = {"authorization": f"Bearer {token}"}
    for method, path in sorted(catalog.human_only_enforced):
        names = [field for _, field, _, _ in string.Formatter().parse(path) if field]
        url = path.format(**{name: (path_values or {}).get(name, "1") for name in names})
        response = client.request(method, url, headers=headers, json={})
        assert response.status_code == 403, f"{method} {path} → {response.status_code}"
