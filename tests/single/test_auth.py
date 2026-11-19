"""Single-user auth: one account, registration closes once bootstrapped."""

from tests.apps import single


def test_status_has_no_site_name(client):
    assert client.get("/api/auth/status").json() == {"bootstrapped": False, "authenticated": False, "user": None}


def test_there_are_no_user_routes():
    paths = single.app.openapi()["paths"]
    assert not any(p.startswith("/api/auth/users") for p in paths)
    assert "/api/auth/tokens" in paths


def test_register_keeps_the_names_as_given_and_closes_afterwards(client, fake_passkeys):
    body = {"username": "Alex", "display_name": "Alex A."}
    options = client.post("/api/auth/register/begin", json=body).json()
    assert options["user"]["name"] == "Alex" and options["rp"]["name"] == "single"
    done = client.post("/api/auth/register/complete", json={**body, "credential": fake_passkeys.registration(options)})
    assert done.json() == {
        "id": "00000000-0000-0000-0000-000000000000",
        "username": "Alex",
        "display_name": "Alex A.",
        "is_admin": True,
    }
    assert client.post("/api/auth/register/begin", json=body).status_code == 409
    assert client.post("/api/auth/register/complete", json={**body, "credential": {}}).status_code == 409


def test_register_needs_both_names(client):
    for body in ({"username": " ", "display_name": "A"}, {"username": "a", "display_name": ""}):
        assert client.post("/api/auth/register/begin", json=body).status_code == 422


def test_sign_in_with_the_registered_passkey(client, fake_passkeys):
    body = {"username": "Alex", "display_name": "Alex"}
    options = client.post("/api/auth/register/begin", json=body).json()
    client.post("/api/auth/register/complete", json={**body, "credential": fake_passkeys.registration(options)})
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    options = client.post("/api/auth/login/begin", json={"username": "Alex"}).json()
    assert len(options["allowCredentials"]) == 1
    assert client.post("/api/auth/login/begin", json={"username": "alex"}).json().get("allowCredentials") in (None, [])
    response = client.post("/api/auth/login/complete", json={"credential": fake_passkeys.assertion(options)})
    assert response.status_code == 200 and response.json()["username"] == "Alex"
    assert client.get("/api/auth/me").status_code == 200


def test_status_answers_from_the_cookie_not_a_token(client, api_token):
    data = client.get("/api/auth/status", headers={"authorization": f"Bearer {api_token}"}).json()
    assert data["authenticated"] is False


def test_an_unknown_credential_is_refused(client, fake_passkeys):
    options = client.post("/api/auth/login/begin", json={}).json()
    response = client.post("/api/auth/login/complete", json={"credential": fake_passkeys.assertion(options, "nope")})
    assert response.status_code == 400 and response.json()["detail"] == "unknown credential"


def test_require_actor_marks_agents(auth_client, client, api_token, house_auth):
    assert auth_client.get("/api/whoami-actor").json() == {"actor": "alex"}
    assert auth_client.get("/api/whoami-actor", headers={house_auth.VIA_HEADER: "mcp"}).json() == {
        "actor": "alex (agent)"
    }
    auth_client.cookies.clear()
    headers = {"authorization": f"Bearer {api_token}"}
    assert client.get("/api/whoami-actor", headers=headers).json() == {"actor": "alex (agent)"}


def test_two_apps_in_one_process_keep_their_own_names():
    from tests.apps import multi

    assert single.auth.SESSION_COOKIE_NAME == "single_session" and multi.auth.SESSION_COOKIE_NAME == "multi_session"
    assert single.auth.VIA_HEADER == "x-single-via" and single.auth.tokens.prefix == "single_"
    assert single.auth.rate_limiter is not multi.auth.rate_limiter
