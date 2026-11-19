"""Own passkeys and sessions (the human-only account routes)."""

from datetime import timedelta


def test_add_rename_and_delete_a_passkey(client, fake_passkeys, house_auth):
    options = client.post("/api/auth/register/begin", json={"username": "alex", "display_name": "Alex"}).json()
    client.post(
        "/api/auth/register/complete",
        json={"username": "alex", "display_name": "Alex", "credential": fake_passkeys.registration(options)},
    )
    first = fake_passkeys.credential_id("passkey-1")

    # The only passkey cannot go.
    assert client.delete(f"/api/auth/credentials/{first}").status_code == 409

    options = client.post("/api/auth/credentials/begin").json()
    assert [c["id"] for c in options["excludeCredentials"]] == [first]
    added = client.post(
        "/api/auth/credentials/complete",
        json={"credential": fake_passkeys.registration(options, "passkey-2"), "nickname": " Laptop "},
    ).json()
    assert added["nickname"] == "Laptop" and added["transports"] == "internal" and added["created"].endswith("Z")

    renamed = client.patch(f"/api/auth/credentials/{added['credential_id']}", json={"nickname": "Phone"}).json()
    assert renamed["nickname"] == "Phone"
    assert [c["nickname"] for c in client.get("/api/auth/credentials").json()] == [None, "Phone"]
    assert client.delete(f"/api/auth/credentials/{first}").status_code == 204
    assert client.patch("/api/auth/credentials/nope", json={"nickname": "x"}).status_code == 404
    assert client.delete("/api/auth/credentials/nope").status_code == 404


def test_a_replayed_sign_count_is_refused(client, fake_passkeys, session, house_auth):
    options = client.post("/api/auth/register/begin", json={"username": "alex", "display_name": "Alex"}).json()
    client.post(
        "/api/auth/register/complete",
        json={"username": "alex", "display_name": "Alex", "credential": fake_passkeys.registration(options)},
    )
    cred = session.get(house_auth.models.WebAuthnCredential, fake_passkeys.credential_id("passkey-1"))
    cred.sign_count = 5
    session.commit()
    options = client.post("/api/auth/login/begin", json={}).json()
    response = client.post("/api/auth/login/complete", json={"credential": fake_passkeys.assertion(options)})
    assert response.status_code == 400 and "sign_count" in response.json()["detail"]


def test_list_and_revoke_sessions(auth_client, session, house_auth, admin, login_as):
    other = house_auth.service.create_session(session, user_id=admin.id, user_agent="Other browser")
    listed = auth_client.get("/api/auth/sessions").json()
    assert {s["session_id"] for s in listed} == {f"session-{admin.id}", other.session_id}
    assert all(s["expires_at"].endswith("Z") for s in listed)
    assert auth_client.delete(f"/api/auth/sessions/{other.session_id}").status_code == 204
    assert auth_client.delete("/api/auth/sessions/nope").status_code == 404
    assert len(auth_client.get("/api/auth/sessions").json()) == 1


def test_sessions_list_drops_expired_ones(auth_client, session, house_auth, admin):
    old = house_auth.service.create_session(session, user_id=admin.id, user_agent=None)
    old.expires_at = old.expires_at - timedelta(days=60)
    session.commit()
    assert [s["session_id"] for s in auth_client.get("/api/auth/sessions").json()] == [f"session-{admin.id}"]
