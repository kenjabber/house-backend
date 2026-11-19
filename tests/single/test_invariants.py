"""The security behaviour of PLAN D-14, one test per line (the /mcp line is in
tests/multi/test_mcp.py). A change here is Ken's decision, recorded in
PLAN.md first."""

import hashlib
import logging
import sqlite3
import stat
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from house_backend.auth import BOOTSTRAP_USER_ID
from tests.apps import single

# ── A presented bearer token is always verified, even under AUTH_DISABLED, and
#    a wrong one is a 401, never a fall-through to another credential.


def test_a_wrong_token_is_401_even_with_a_valid_cookie(auth_client):
    response = auth_client.get("/api/auth/me", headers={"authorization": "Bearer single_wrong"})
    assert response.status_code == 401 and response.json()["detail"] == "invalid API token"


def test_a_wrong_token_is_401_under_auth_disabled(client, monkeypatch, house_auth):
    monkeypatch.setattr(house_auth.settings, "AUTH_DISABLED", True)
    assert client.get("/api/auth/me").status_code == 200  # the stand-in admin
    assert client.get("/api/auth/me", headers={"authorization": "Bearer single_wrong"}).status_code == 401


# ── Only the SHA-256 of an API token is stored; the plaintext is shown once.


def test_only_the_hash_of_a_token_is_stored(auth_client, engine):
    token = auth_client.post("/api/auth/tokens", json={"name": "agent"}).json()["token"]
    con = sqlite3.connect(engine.url.database)
    rows = con.execute("SELECT * FROM api_token").fetchall()
    con.close()
    assert len(rows) == 1
    assert hashlib.sha256(token.encode()).hexdigest() in rows[0]
    assert not any(token in str(value) for value in rows[0])
    assert all("token" not in t for t in auth_client.get("/api/auth/tokens").json())


# ── A request made by the MCP layer is marked with the via header, which can
#    only reduce privilege; require_human refuses token and MCP requests.


def test_require_human_refuses_a_token(client, api_token):
    response = client.get("/api/auth/sessions", headers={"authorization": f"Bearer {api_token}"})
    assert response.status_code == 403 and "human-only" in response.json()["detail"]


def test_the_via_header_only_reduces_privilege(client, auth_client, house_auth):
    via = {house_auth.VIA_HEADER: "MCP"}
    assert auth_client.get("/api/auth/sessions", headers=via).status_code == 403  # a cookie plus the mark
    assert auth_client.get("/api/auth/me", headers=via).status_code == 200  # still the user, nothing more
    auth_client.cookies.clear()
    assert client.get("/api/auth/me", headers=via).status_code == 401  # the mark alone is no credential


# ── An expired session is deleted when it is next presented; WebAuthn
#    challenges are single-use and expire; auth routes are rate-limited per
#    address.


def test_an_expired_session_is_deleted_when_presented(auth_client, session, admin, house_auth):
    model = house_auth.models.UserSession
    row = session.get(model, f"session-{admin.id}")
    row.expires_at = row.expires_at - timedelta(days=2)
    session.commit()
    response = auth_client.get("/api/auth/me")
    assert response.status_code == 401 and response.json()["detail"] == "session expired"
    session.expire_all()
    assert session.get(model, f"session-{admin.id}") is None


def test_a_challenge_is_single_use(client, fake_passkeys):
    body = {"username": "Alex", "display_name": "Alex"}
    options = client.post("/api/auth/register/begin", json=body).json()
    credential = fake_passkeys.registration(options)
    assert client.post("/api/auth/register/complete", json={**body, "credential": credential}).status_code == 200
    client.post("/api/auth/logout")
    options = client.post("/api/auth/login/begin", json={}).json()
    assertion = fake_passkeys.assertion(options)
    assert client.post("/api/auth/login/complete", json={"credential": assertion}).status_code == 200
    replay = client.post("/api/auth/login/complete", json={"credential": assertion})
    assert replay.status_code == 400 and "challenge invalid or expired" in replay.json()["detail"]


def test_a_challenge_expires(client, fake_passkeys, session, house_auth):
    body = {"username": "Alex", "display_name": "Alex"}
    options = client.post("/api/auth/register/begin", json=body).json()
    challenge = session.get(house_auth.models.WebAuthnChallenge, options["challenge"])
    challenge.expires_at = challenge.expires_at - timedelta(minutes=10)
    session.commit()
    late = client.post("/api/auth/register/complete", json={**body, "credential": fake_passkeys.registration(options)})
    assert late.status_code == 400 and "challenge invalid or expired" in late.json()["detail"]


def test_a_challenge_is_for_its_purpose_only(client, fake_passkeys):
    body = {"username": "Alex", "display_name": "Alex"}
    login_options = client.post("/api/auth/login/begin", json={}).json()
    response = client.post(
        "/api/auth/register/complete", json={**body, "credential": fake_passkeys.registration(login_options)}
    )
    assert response.status_code == 400


def test_auth_routes_are_rate_limited_per_address(client, monkeypatch, house_auth):
    monkeypatch.setattr(house_auth.settings, "AUTH_RATE_LIMIT_PER_MINUTE", 3)
    codes = [client.post("/api/auth/login/begin", json={}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert house_auth.rate_limiter.allow("192.0.2.1", 3)  # another address has its own budget


# ── AUTH_DISABLED logs the loud warning at start-up and makes every request
#    the bootstrap admin.


def test_auth_disabled_warns_at_start_up(engine, monkeypatch, house_auth, caplog):
    monkeypatch.setattr(house_auth.settings, "AUTH_DISABLED", True)
    with caplog.at_level(logging.WARNING), TestClient(single.app):
        pass
    assert any("AUTH_DISABLED=true" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


def test_auth_disabled_makes_every_request_the_bootstrap_admin(client, monkeypatch, house_auth):
    monkeypatch.setattr(house_auth.settings, "AUTH_DISABLED", True)
    me = client.get("/api/auth/me").json()
    assert me["id"] == BOOTSTRAP_USER_ID and me["is_admin"]
    assert client.get("/api/auth/sessions").status_code == 200


def test_auth_is_on_without_the_setting(client):
    assert client.get("/api/auth/me").status_code == 401


# ── The database file is made mode 600 at start-up.


def test_the_database_file_is_mode_600_after_start_up(engine, house_auth):
    path = Path(house_auth.settings.SQLITE_PATH)
    path.chmod(0o644)
    with TestClient(single.app):
        pass
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
