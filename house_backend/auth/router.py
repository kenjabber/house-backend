"""The ``/auth`` routes: passkey registration and sign-in, the account, its
passkeys, sessions and API tokens, and (multi-user) the admin's users.

17 routes in all; the 5 ``/auth/users`` routes only with ``multi_user``, the 3
``/auth/tokens`` routes only with ``api_tokens``. Paths, request and response
shapes are the apps' existing ones (PLAN D-9).

No ``from __future__ import annotations`` here: the routes are closures over
per-app model classes, which FastAPI must see as objects, not strings.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from house_backend.auth.core import HouseAuth
from house_backend.auth.models import BOOTSTRAP_USER_ID
from house_backend.auth.schemas import (
    ApiTokenCreated,
    ApiTokenCreateRequest,
    ApiTokenResponse,
    CredentialAddCompleteRequest,
    CredentialPatchRequest,
    CredentialResponse,
    LoginBeginRequest,
    LoginCompleteRequest,
    RegisterBeginRequest,
    RegisterCompleteRequest,
    SessionResponse,
    build_schemas,
)


def build_router(auth: HouseAuth) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])
    schemas = build_schemas(auth)
    UserResponse = schemas.UserResponse
    UserDetail = schemas.UserDetail
    AuthStatusResponse = schemas.AuthStatusResponse
    UserCreateRequest = schemas.UserCreateRequest
    UserPatchRequest = schemas.UserPatchRequest

    service = auth.service
    settings = auth.settings
    models = auth.models
    ext = auth.extension
    get_db_session = auth.get_db_session
    require_user = auth.require_user
    require_human = auth.require_human
    require_admin = auth.require_admin
    require_human_admin = auth.require_human_admin

    # ── Helpers ──────────────────────────────────────────────────────────────

    def set_session_cookie(response: Response, session_id: str) -> None:
        response.set_cookie(
            key=auth.SESSION_COOKIE_NAME,
            value=session_id,
            max_age=settings.SESSION_LIFETIME_DAYS * 24 * 60 * 60,
            httponly=True,
            secure=settings.SESSION_COOKIE_SECURE,
            samesite="lax",
            path="/",
        )

    def user_response(session: Session, user: Any) -> Any:
        return UserResponse(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            is_admin=user.is_admin,
            **ext.user_values(session, user),
        )

    def user_detail(session: Session, user: Any) -> Any:
        return UserDetail(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            is_admin=user.is_admin,
            **ext.user_values(session, user),
            has_passkey=service.user_has_credentials(session, user.id),
            created=user.created,
            **ext.detail_values(session, user),
        )

    def clean_username(raw: str) -> str:
        """Multi-user: a username is lower-case letters, digits and ``. _ -``."""
        username = raw.strip().lower()
        if not username or len(username) > 64 or not all(c.isalnum() or c in "._-" for c in username):
            raise HTTPException(status_code=422, detail="username: letters, digits, . _ - only")
        return username

    def single_user_names(body: RegisterBeginRequest | RegisterCompleteRequest) -> tuple[str, str]:
        username = body.username.strip()
        display_name = body.display_name.strip()
        if not username or not display_name:
            raise HTTPException(status_code=422, detail="username and display_name required")
        return username, display_name

    def start_session(request: Request, response: Response, session: Session, user: Any) -> None:
        sess = service.create_session(session, user_id=user.id, user_agent=request.headers.get("user-agent"))
        set_session_cookie(response, sess.session_id)

    # ── Status, registration, sign-in ────────────────────────────────────────

    @router.get("/status", response_model=AuthStatusResponse)
    def auth_status(request: Request, session: Session = Depends(get_db_session)) -> Any:
        bootstrapped = service.is_bootstrapped(session)
        # Single-user apps have always answered /status from the cookie (or the
        # AUTH_DISABLED stand-in) alone; multi-user apps also from a token.
        user = auth.current_user_or_none(request, session, use_token=auth.multi_user)
        extra = {"site_name": settings.SITE_NAME} if auth.multi_user else {}
        return AuthStatusResponse(
            bootstrapped=bootstrapped,
            authenticated=user is not None,
            **extra,
            user=user_response(session, user) if user is not None else None,
        )

    @router.post("/register/begin")
    def register_begin(
        body: RegisterBeginRequest, request: Request, session: Session = Depends(get_db_session)
    ) -> dict[str, Any]:
        """Issue WebAuthn registration options.

        Before the first passkey exists anyone may register and becomes the
        admin (bootstrap). Afterwards, single-user: closed (more passkeys are
        added through ``/auth/credentials``); multi-user: only an account an
        admin created, and that has no passkey yet, may register one.
        """
        auth.enforce_rate_limit(request)
        if not auth.multi_user:
            if service.is_bootstrapped(session):
                raise HTTPException(status_code=409, detail="already bootstrapped")
            username, display_name = single_user_names(body)
            target_user_id = BOOTSTRAP_USER_ID
        else:
            username = clean_username(body.username)
            display_name = body.display_name.strip() or username
            if not service.is_bootstrapped(session):
                target_user_id = BOOTSTRAP_USER_ID
            else:
                user = service.get_user_by_username(session, username)
                if user is None:
                    raise HTTPException(status_code=404, detail="no such account — ask an admin to create it first")
                if service.user_has_credentials(session, user.id):
                    raise HTTPException(status_code=409, detail="this account already has a passkey — sign in instead")
                target_user_id = user.id
        return service.begin_registration(
            session,
            username=username,
            display_name=display_name,
            user_id=target_user_id,
            exclude_credential_ids=[],
        )

    @router.post("/register/complete")
    def register_complete(
        body: RegisterCompleteRequest,
        request: Request,
        response: Response,
        session: Session = Depends(get_db_session),
    ) -> UserResponse:
        """Verify the attestation, bind the passkey to the account, sign in."""
        auth.enforce_rate_limit(request)
        if not auth.multi_user:
            if service.is_bootstrapped(session):
                raise HTTPException(status_code=409, detail="already bootstrapped")
            username, display_name = single_user_names(body)
            user = service.take_over_bootstrap_user(session, username=username, display_name=display_name)
        else:
            username = clean_username(body.username)
            display_name = body.display_name.strip() or username
            if not service.is_bootstrapped(session):
                user = service.take_over_bootstrap_user(session, username=username, display_name=display_name)
            else:
                user = service.get_user_by_username(session, username)
                if user is None:
                    raise HTTPException(status_code=404, detail="no such account")
                if service.user_has_credentials(session, user.id):
                    raise HTTPException(status_code=409, detail="this account already has a passkey")
        try:
            service.complete_registration(session, user_id=user.id, response=body.credential)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        start_session(request, response, session, user)
        return user_response(session, user)

    @router.post("/login/begin")
    def login_begin(
        body: LoginBeginRequest, request: Request, session: Session = Depends(get_db_session)
    ) -> dict[str, Any]:
        auth.enforce_rate_limit(request)
        user = None
        if body.username:
            name = body.username.strip()
            user = service.get_user_by_username(session, name.lower() if auth.multi_user else name)
        return service.begin_authentication(session, user=user)

    @router.post("/login/complete", response_model=UserResponse)
    def login_complete(
        body: LoginCompleteRequest,
        request: Request,
        response: Response,
        session: Session = Depends(get_db_session),
    ) -> Any:
        auth.enforce_rate_limit(request)
        try:
            user = service.complete_authentication(session, response=body.credential)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        start_session(request, response, session, user)
        return user_response(session, user)

    @router.post("/logout", status_code=204)
    def logout(request: Request, response: Response, session: Session = Depends(get_db_session)) -> Response:
        sid = request.cookies.get(auth.SESSION_COOKIE_NAME)
        if sid:
            service.delete_session(session, sid)
        response.delete_cookie(auth.SESSION_COOKIE_NAME, path="/")
        return Response(status_code=204)

    @router.get("/me", response_model=UserResponse)
    def whoami(user: Any = Depends(require_user), session: Session = Depends(get_db_session)) -> Any:
        return user_response(session, user)

    # ── Users (multi-user; admin) ────────────────────────────────────────────

    # Listing is open to an admin's API token (an agent needs the ids to grant
    # access); creating, changing and deleting accounts stays with a person.

    if auth.multi_user:

        @router.get("/users", response_model=list[UserDetail])
        def list_users(_admin: Any = Depends(require_admin), session: Session = Depends(get_db_session)) -> Any:
            return [user_detail(session, u) for u in service.list_all_users(session)]

        @router.post("/users", response_model=UserDetail, status_code=201)
        def create_user(
            body: UserCreateRequest,
            _admin: Any = Depends(require_human_admin),
            session: Session = Depends(get_db_session),
        ) -> Any:
            """Create an account without a passkey; its owner registers one at /register."""
            username = clean_username(body.username)
            if service.get_user_by_username(session, username) is not None:
                raise HTTPException(status_code=409, detail="username already taken")
            extra = {
                name: getattr(body, name) for name in (ext.create_fields.model_fields if ext.create_fields else {})
            }
            values = ext.new_user_values(session, extra)
            user = service.create_user(
                session,
                username=username,
                display_name=body.display_name.strip() or username,
                is_admin=body.is_admin,
                **values,
            )
            return user_detail(session, user)

        @router.patch("/users/{user_id}", response_model=UserDetail)
        def patch_user(
            user_id: str,
            body: UserPatchRequest,
            admin: Any = Depends(require_human_admin),
            session: Session = Depends(get_db_session),
        ) -> Any:
            user = session.get(models.User, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="user not found")
            if body.display_name is not None:
                user.display_name = body.display_name.strip()[:120] or user.username
            if body.is_admin is not None:
                if user.id == admin.id and not body.is_admin:
                    raise HTTPException(status_code=409, detail="you cannot remove your own admin role")
                user.is_admin = body.is_admin
            if ext.patch_fields is not None:
                values = {n: getattr(body, n) for n in ext.patch_fields.model_fields if n in body.model_fields_set}
                ext.update_user(session, user, values)
            session.commit()
            return user_detail(session, user)

        @router.delete("/users/{user_id}", status_code=204)
        def delete_user(
            user_id: str, admin: Any = Depends(require_human_admin), session: Session = Depends(get_db_session)
        ) -> Response:
            user = session.get(models.User, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="user not found")
            if user.id == admin.id:
                raise HTTPException(status_code=409, detail="you cannot delete your own account")
            session.delete(user)
            session.commit()
            return Response(status_code=204)

        @router.post("/users/{user_id}/reset-passkeys", response_model=UserDetail)
        def reset_user_passkeys(
            user_id: str, admin: Any = Depends(require_human_admin), session: Session = Depends(get_db_session)
        ) -> Any:
            """Remove a user's passkeys (and sessions) so they can register a new one."""
            user = session.get(models.User, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="user not found")
            if user.id == admin.id:
                raise HTTPException(status_code=409, detail="manage your own passkeys from the Account tab")
            service.delete_user_credentials(session, user.id)
            session.query(models.UserSession).filter(models.UserSession.user_id == user.id).delete(
                synchronize_session=False
            )
            session.commit()
            return user_detail(session, user)

    # ── Own passkeys and sessions (human-only) ───────────────────────────────

    @router.get("/credentials", response_model=list[CredentialResponse])
    def list_credentials(user: Any = Depends(require_human), session: Session = Depends(get_db_session)) -> Any:
        return [CredentialResponse.model_validate(c) for c in service.list_user_credentials(session, user.id)]

    @router.post("/credentials/begin")
    def credentials_add_begin(
        request: Request, user: Any = Depends(require_human), session: Session = Depends(get_db_session)
    ) -> dict[str, Any]:
        auth.enforce_rate_limit(request)
        existing = [c.credential_id for c in service.list_user_credentials(session, user.id)]
        return service.begin_registration(
            session,
            username=user.username,
            display_name=user.display_name,
            user_id=user.id,
            exclude_credential_ids=existing,
        )

    @router.post("/credentials/complete", response_model=CredentialResponse)
    def credentials_add_complete(
        body: CredentialAddCompleteRequest,
        user: Any = Depends(require_human),
        session: Session = Depends(get_db_session),
    ) -> Any:
        try:
            cred = service.complete_registration(session, user_id=user.id, response=body.credential)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if body.nickname is not None:
            cred.nickname = body.nickname.strip()[:120] or None
            session.commit()
        return CredentialResponse.model_validate(cred)

    @router.patch("/credentials/{credential_id}", response_model=CredentialResponse)
    def credentials_patch(
        credential_id: str,
        body: CredentialPatchRequest,
        user: Any = Depends(require_human),
        session: Session = Depends(get_db_session),
    ) -> Any:
        cred = session.get(models.WebAuthnCredential, credential_id)
        if cred is None or cred.user_id != user.id:
            raise HTTPException(status_code=404, detail="credential not found")
        if body.nickname is not None:
            cred.nickname = body.nickname.strip()[:120] or None
        session.commit()
        return CredentialResponse.model_validate(cred)

    @router.delete("/credentials/{credential_id}", status_code=204)
    def credentials_delete(
        credential_id: str, user: Any = Depends(require_human), session: Session = Depends(get_db_session)
    ) -> Response:
        cred = session.get(models.WebAuthnCredential, credential_id)
        if cred is None or cred.user_id != user.id:
            raise HTTPException(status_code=404, detail="credential not found")
        remaining = (
            session.query(models.WebAuthnCredential).filter(models.WebAuthnCredential.user_id == user.id).count()
        )
        # The last passkey stays: deleting it would lock the account out.
        if remaining <= 1:
            raise HTTPException(status_code=409, detail="cannot delete the only remaining passkey on this account")
        session.delete(cred)
        session.commit()
        return Response(status_code=204)

    @router.get("/sessions", response_model=list[SessionResponse])
    def list_sessions(user: Any = Depends(require_human), session: Session = Depends(get_db_session)) -> Any:
        return [SessionResponse.model_validate(s) for s in service.list_user_sessions(session, user.id)]

    @router.delete("/sessions/{session_id}", status_code=204)
    def revoke_session(
        session_id: str, user: Any = Depends(require_human), session: Session = Depends(get_db_session)
    ) -> Response:
        row = session.get(models.UserSession, session_id)
        if row is None or row.user_id != user.id:
            raise HTTPException(status_code=404, detail="session not found")
        session.delete(row)
        session.commit()
        return Response(status_code=204)

    # ── API tokens (the MCP credential; human-only) ──────────────────────────
    # An agent must never be able to mint or revoke its own credential.

    if auth.api_tokens:

        @router.get("/tokens", response_model=list[ApiTokenResponse])
        def list_api_tokens(user: Any = Depends(require_human), session: Session = Depends(get_db_session)) -> Any:
            return [ApiTokenResponse.model_validate(t) for t in auth.tokens.list_tokens(session, user.id)]

        @router.post("/tokens", response_model=ApiTokenCreated, status_code=201)
        def create_api_token(
            body: ApiTokenCreateRequest,
            user: Any = Depends(require_human),
            session: Session = Depends(get_db_session),
        ) -> Any:
            name = body.name.strip()[:120]
            if not name:
                raise HTTPException(status_code=400, detail="token name is required")
            row, plaintext = auth.tokens.create_token(session, user_id=user.id, name=name)
            return ApiTokenCreated(
                id=row.id,
                name=row.name,
                prefix=row.prefix,
                created=row.created,
                last_used_at=row.last_used_at,
                token=plaintext,
            )

        @router.delete("/tokens/{token_id}", status_code=204)
        def revoke_api_token(
            token_id: int, user: Any = Depends(require_human), session: Session = Depends(get_db_session)
        ) -> Response:
            row = session.get(models.ApiToken, token_id)
            if row is None or row.user_id != user.id:
                raise HTTPException(status_code=404, detail="token not found")
            session.delete(row)
            session.commit()
            return Response(status_code=204)

    return router
