"""Multi-user auth: status, bootstrap and invited registration, user
management with the app's extension fields, tokens, hold-backs. Ported from
the multi-user apps' own suites."""

from tests.apps.multi import Team


def _give_passkey(session, house_auth, user_id: str) -> None:
    model = house_auth.models.WebAuthnCredential
    session.add(model(credential_id=f"cred-{user_id}", user_id=user_id, public_key=b"k", sign_count=0))
    session.commit()


def test_status_reports_bootstrap_and_site_name(client):
    data = client.get("/api/auth/status").json()
    assert data == {"bootstrapped": False, "authenticated": False, "site_name": "Multi", "user": None}


def test_status_reports_the_signed_in_user_with_extension_fields(auth_client, admin):
    data = auth_client.get("/api/auth/status").json()
    assert data["authenticated"] and data["user"] == {
        "id": admin.id,
        "username": "alex",
        "display_name": "Alex",
        "is_admin": True,
        "team_id": admin.team_id,
    }


def test_protected_routes_require_a_session(client):
    for path in ("/api/auth/me", "/api/auth/users", "/api/teams", "/api/auth/tokens", "/api/auth/sessions"):
        assert client.get(path).status_code == 401, path


def test_register_begin_is_open_before_bootstrap_only(client, session, seed_user, house_auth):
    options = client.post("/api/auth/register/begin", json={"username": "Alex", "display_name": "Alex"}).json()
    assert options["rp"]["id"] == "localhost" and options["user"]["name"] == "alex"
    admin = seed_user()
    _give_passkey(session, house_auth, admin.id)
    assert client.post("/api/auth/register/begin", json={"username": "nobody", "display_name": "x"}).status_code == 404
    seed_user(user_id="u2", username="sam", is_admin=False)
    assert client.post("/api/auth/register/begin", json={"username": "sam", "display_name": "Sam"}).status_code == 200
    _give_passkey(session, house_auth, "u2")
    assert client.post("/api/auth/register/begin", json={"username": "sam", "display_name": "Sam"}).status_code == 409


def test_register_refuses_a_bad_username(client):
    response = client.post("/api/auth/register/begin", json={"username": "bad name!", "display_name": "x"})
    assert response.status_code == 422


def test_first_registration_bootstraps_the_admin_with_extension_values(client, session, fake_passkeys):
    options = client.post("/api/auth/register/begin", json={"username": "Alex", "display_name": ""}).json()
    done = client.post(
        "/api/auth/register/complete",
        json={"username": "Alex", "display_name": "", "credential": fake_passkeys.registration(options)},
    )
    assert done.status_code == 200, done.text
    user = done.json()
    assert user["id"] == "00000000-0000-0000-0000-000000000000"
    assert user["username"] == "alex" and user["display_name"] == "alex" and user["is_admin"]
    assert session.get(Team, user["team_id"]).name == "Team"
    assert client.get("/api/auth/me").json() == user  # signed in by the cookie
    assert client.get("/api/auth/status").json()["bootstrapped"] is True


def test_an_invited_user_registers_and_signs_in(client, auth_client, admin, session, house_auth, fake_passkeys):
    _give_passkey(session, house_auth, admin.id)  # bootstrapped
    created = auth_client.post("/api/auth/users", json={"username": "sam", "team_id": 1}).json()
    client.cookies.clear()
    options = client.post("/api/auth/register/begin", json={"username": "sam", "display_name": "Sam"}).json()
    registered = client.post(
        "/api/auth/register/complete",
        json={"username": "sam", "display_name": "Sam", "credential": fake_passkeys.registration(options, "sams")},
    )
    assert registered.json()["id"] == created["id"]
    client.cookies.clear()
    options = client.post("/api/auth/login/begin", json={"username": "SAM"}).json()
    assert [c["id"] for c in options["allowCredentials"]] == [fake_passkeys.credential_id("sams")]
    signed_in = client.post("/api/auth/login/complete", json={"credential": fake_passkeys.assertion(options, "sams")})
    assert signed_in.status_code == 200 and signed_in.json()["username"] == "sam"
    assert client.get("/api/auth/me").json()["id"] == created["id"]


def test_admin_creates_lists_and_deletes_users(auth_client, admin):
    team = admin.team_id
    created = auth_client.post(
        "/api/auth/users", json={"username": "Sam.D", "display_name": "Sam", "team_id": team}
    ).json()
    assert created["username"] == "sam.d" and created["has_passkey"] is False and not created["is_admin"]
    assert created["team_id"] == team and created["token_count"] == 0
    assert auth_client.post("/api/auth/users", json={"username": "sam.d", "team_id": team}).status_code == 409
    assert auth_client.post("/api/auth/users", json={"username": "bad name!", "team_id": team}).status_code == 422
    assert auth_client.post("/api/auth/users", json={"username": "x", "team_id": 999}).status_code == 422
    assert auth_client.post("/api/auth/users", json={"username": "y"}).status_code == 422  # team_id required
    users = auth_client.get("/api/auth/users").json()
    assert {u["username"] for u in users} == {"alex", "sam.d"}
    patched = auth_client.patch(f"/api/auth/users/{created['id']}", json={"is_admin": True, "display_name": "S"}).json()
    assert patched["is_admin"] and patched["display_name"] == "S" and patched["team_id"] == team
    assert auth_client.delete(f"/api/auth/users/{created['id']}").status_code == 204
    assert len(auth_client.get("/api/auth/users").json()) == 1


def test_patch_applies_only_the_extension_fields_sent(auth_client, admin, session):
    other = Team(name="Other")
    session.add(other)
    session.commit()
    url = f"/api/auth/users/{admin.id}"
    assert auth_client.patch(url, json={"team_id": 999}).status_code == 422
    assert auth_client.patch(url, json={"team_id": other.id}).json()["team_id"] == other.id
    assert auth_client.patch(url, json={"display_name": "A"}).json()["team_id"] == other.id
    assert auth_client.get("/api/auth/me").json()["team_id"] == other.id


def test_the_detail_carries_computed_extension_values(auth_client, api_token):
    assert auth_client.get("/api/auth/users").json()[0]["token_count"] == 1


def test_admin_cannot_demote_or_delete_self(auth_client, admin):
    assert auth_client.patch(f"/api/auth/users/{admin.id}", json={"is_admin": False}).status_code == 409
    assert auth_client.delete(f"/api/auth/users/{admin.id}").status_code == 409
    assert auth_client.post(f"/api/auth/users/{admin.id}/reset-passkeys").status_code == 409


def test_unknown_users_are_404(auth_client):
    assert auth_client.patch("/api/auth/users/nobody", json={}).status_code == 404
    assert auth_client.delete("/api/auth/users/nobody").status_code == 404
    assert auth_client.post("/api/auth/users/nobody/reset-passkeys").status_code == 404


def test_reset_passkeys_clears_credentials_and_sessions(auth_client, session, seed_user, login_as, house_auth):
    sam = seed_user(user_id="u2", username="sam", is_admin=False)
    _give_passkey(session, house_auth, sam.id)
    login_as(auth_client.__class__(auth_client.app), sam)  # a session for sam
    assert auth_client.get("/api/auth/users").json()[1]["has_passkey"] is True
    out = auth_client.post(f"/api/auth/users/{sam.id}/reset-passkeys").json()
    assert out["has_passkey"] is False
    model = house_auth.models.UserSession
    session.expire_all()
    assert session.query(model).filter(model.user_id == sam.id).count() == 0


def test_member_is_not_an_admin(member_client):
    assert member_client.get("/api/auth/users").status_code == 403
    assert member_client.post("/api/auth/users", json={"username": "x", "team_id": 1}).status_code == 403
    assert member_client.post("/api/teams", json={"name": "x"}).status_code == 403
    assert member_client.get("/api/teams").status_code == 200


def test_api_token_authenticates_but_not_for_human_only(client, api_token):
    headers = {"authorization": f"Bearer {api_token}"}
    assert client.get("/api/auth/me", headers=headers).json()["username"] == "alex"
    assert client.get("/api/auth/users", headers=headers).status_code == 200  # listing is open to an admin's token
    assert client.post("/api/auth/users", json={"username": "x", "team_id": 1}, headers=headers).status_code == 403
    assert client.get("/api/auth/tokens", headers=headers).status_code == 403
    assert client.get("/api/auth/credentials", headers=headers).status_code == 403
    assert client.get("/api/auth/me", headers={"authorization": "Bearer multi_wrong"}).status_code == 401


def test_status_recognises_a_token(client, api_token):
    data = client.get("/api/auth/status", headers={"authorization": f"Bearer {api_token}"}).json()
    assert data["authenticated"] and data["user"]["username"] == "alex"


def test_tokens_are_created_once_and_revocable(auth_client):
    created = auth_client.post("/api/auth/tokens", json={"name": "claude"}).json()
    assert created["token"].startswith("multi_") and created["prefix"] == created["token"][:10]
    assert created["created"].endswith("Z")
    listed = auth_client.get("/api/auth/tokens").json()
    assert len(listed) == 1 and "token" not in listed[0]
    assert auth_client.post("/api/auth/tokens", json={"name": "  "}).status_code == 400
    assert auth_client.delete(f"/api/auth/tokens/{created['id']}").status_code == 204
    assert auth_client.delete(f"/api/auth/tokens/{created['id']}").status_code == 404
    assert auth_client.get("/api/auth/tokens").json() == []


def test_logout_clears_the_session(auth_client):
    assert auth_client.get("/api/auth/me").status_code == 200
    assert auth_client.post("/api/auth/logout").status_code == 204
    assert auth_client.get("/api/auth/me").status_code == 401


def test_dev_bypass_signs_in_as_bootstrap_admin_with_extension_values(client, monkeypatch, house_auth):
    monkeypatch.setattr(house_auth.settings, "AUTH_DISABLED", True)
    status = client.get("/api/auth/status").json()
    assert status["authenticated"] and status["user"]["is_admin"] and status["user"]["team_id"] == 1
    assert client.get("/api/teams").status_code == 200


def test_every_shared_scalar_field_is_plain_and_no_model_field_is():
    """D-16: a scalar field of a shared response is marked x-house: plain; a
    field holding a model is not, so the model's own fields (an app's
    extension fields among them) are classified one by one."""
    from tests.apps import multi

    schemas = multi.app.openapi()["components"]["schemas"]
    shared = [
        "UserResponse",
        "UserDetail",
        "AuthStatusResponse",
        "CredentialResponse",
        "SessionResponse",
        "ApiTokenResponse",
        "ApiTokenCreated",
    ]
    extension = {"team_id", "token_count"}
    for name in shared:
        for field, prop in schemas[name]["properties"].items():
            holds_model = "$ref" in str(prop)
            if field in extension:
                assert "x-house" not in prop, f"{name}.{field}"
            elif holds_model:
                assert "x-house" not in prop, f"{name}.{field} holds a model"
            else:
                assert prop.get("x-house") == "plain", f"{name}.{field}"
