"""``HouseAuth``: one object configures an app's auth (PLAN D-11).

::

    auth = HouseAuth(
        app_name="example",          # cookie "example_session", header "x-example-via"
        settings=settings,           # a HouseSettings
        models=AuthModels(User, WebAuthnCredential, UserSession, WebAuthnChallenge, ApiToken),
        multi_user=True,
        api_tokens=True,
    )
    require_user = auth.require_user   # the FastAPI dependencies, re-exported
    app.include_router(auth.router(), prefix="/api")

Nothing is module-level: two apps (or two test apps) in one process each have
their own settings, models and rate-limit table.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator
from typing import Any, NamedTuple

from fastapi import Depends, HTTPException, Request
from fastapi.routing import APIRouter
from sqlalchemy.orm import Session

from house_backend.auth.extension import UserExtension
from house_backend.auth.ratelimit import RateLimiter
from house_backend.auth.service import PasskeyService
from house_backend.auth.tokens import TokenService
from house_backend.settings import HouseSettings

logger = logging.getLogger(__name__)

HUMAN_ONLY_DETAIL = "human-only action: not available to API tokens or MCP"


class AuthModels(NamedTuple):
    """The app's five auth model classes (built on the mixins of
    :mod:`house_backend.auth.models`). ``ApiToken`` is ``None`` when the app
    has no API tokens."""

    User: Any
    WebAuthnCredential: Any
    UserSession: Any
    WebAuthnChallenge: Any
    ApiToken: Any = None


class HouseAuth:
    """The app's auth: settings, models, options, services and dependencies.

    Options (D-12):

    ``multi_user``
        Off: one account, the first registrant; ``register/*`` closes once a
        passkey exists. On: the first registrant bootstraps the admin, an admin
        creates every later account (the five ``/auth/users`` routes) and its
        owner registers a passkey for it; usernames are lower-cased and checked;
        ``/auth/status`` also carries ``site_name``.
    ``api_tokens``
        On: the bearer-token credential, the three ``/auth/tokens`` routes, and
        a token accepted by every ``require_*`` dependency and by ``/mcp``.
    ``extension``
        What the app adds to a user (:class:`UserExtension`).
    """

    def __init__(
        self,
        *,
        app_name: str,
        settings: HouseSettings,
        models: AuthModels,
        multi_user: bool = False,
        api_tokens: bool = True,
        extension: UserExtension | None = None,
    ):
        if not app_name.isidentifier() or app_name != app_name.lower():
            raise ValueError("app_name must be a lower-case identifier (it names the cookie and the header)")
        if api_tokens and models.ApiToken is None:
            raise ValueError("api_tokens=True needs an ApiToken model")
        self.app_name = app_name
        self.settings = settings
        self.models = models
        self.multi_user = multi_user
        self.api_tokens = api_tokens
        self.extension = extension or UserExtension()

        self.SESSION_COOKIE_NAME = f"{app_name}_session"
        # Set by the MCP layer on every in-process REST call it makes. It can
        # only reduce privilege (it marks the request as an agent's), so
        # trusting a client-supplied value is safe.
        self.VIA_HEADER = f"x-{app_name}-via"
        self.VIA_MCP = "mcp"

        self.service = PasskeyService(self)
        self.tokens = TokenService(self)
        self.rate_limiter = RateLimiter()
        self._build_dependencies()

    # ── Plain helpers (not dependencies) ─────────────────────────────────────

    def bearer_token(self, request: Request) -> str | None:
        """The token of an ``Authorization: Bearer …`` header, if one was sent."""
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
        return None

    def is_agent_request(self, request: Request) -> bool:
        """True for a request made by an agent rather than a browser session:
        authenticated with an API token, or issued by the MCP layer."""
        return (
            self.bearer_token(request) is not None or request.headers.get(self.VIA_HEADER, "").lower() == self.VIA_MCP
        )

    def current_user_or_none(self, request: Request, session: Session, *, use_token: bool = True) -> Any | None:
        """The signed-in user (token, ``AUTH_DISABLED`` stand-in, or cookie), or
        ``None``. Never raises; an unknown token gives ``None``."""
        token = self.bearer_token(request) if use_token and self.api_tokens else None
        if token is not None:
            return self.tokens.user_for_token(session, token)
        if self.settings.AUTH_DISABLED:
            return self.service.dev_bypass_user(session)
        sid = request.cookies.get(self.SESSION_COOKIE_NAME)
        if not sid:
            return None
        return self.service.load_user_for_session(session, sid)

    def client_ip(self, request: Request) -> str:
        return (request.client.host if request.client else "unknown") or "unknown"

    def enforce_rate_limit(self, request: Request) -> None:
        """429 once the client's address has made too many auth calls this minute."""
        if not self.rate_limiter.allow(self.client_ip(request), self.settings.AUTH_RATE_LIMIT_PER_MINUTE):
            raise HTTPException(status_code=429, detail="too many auth attempts")

    def warn_if_auth_disabled(self, log: logging.Logger | None = None) -> None:
        """The loud start-up warning of ``AUTH_DISABLED``."""
        if self.settings.AUTH_DISABLED:
            (log or logger).warning(
                "!!! AUTH_DISABLED=true — passkey login is BYPASSED and every "
                "request runs as the bootstrap admin. Development only; never "
                "deploy with this set."
            )

    def router(self) -> APIRouter:
        """The ``/auth`` router; the app includes it under ``/api``."""
        from house_backend.auth.router import build_router

        return build_router(self)

    # ── FastAPI dependencies ─────────────────────────────────────────────────

    # Declared here for type checkers; built per instance by _build_dependencies.
    get_db_session: Callable[..., Generator[Session]]
    require_user: Callable[..., Any]
    require_human: Callable[..., Any]
    require_admin: Callable[..., Any]
    require_human_admin: Callable[..., Any]
    require_actor: Callable[..., str]

    def _build_dependencies(self) -> None:
        auth = self

        def get_db_session(request: Request) -> Generator[Session]:
            session: Session = request.app.state.SessionFactory()
            try:
                yield session
            finally:
                session.close()

        def require_user(request: Request, session: Session = Depends(get_db_session)) -> Any:
            # A presented bearer token is always verified, even under
            # AUTH_DISABLED, so a wrong token never falls through to another
            # credential.
            token = auth.bearer_token(request) if auth.api_tokens else None
            if token is not None:
                user = auth.tokens.user_for_token(session, token)
                if user is None:
                    raise HTTPException(status_code=401, detail="invalid API token")
                return user
            if auth.settings.AUTH_DISABLED:
                return auth.service.dev_bypass_user(session)
            session_id = request.cookies.get(auth.SESSION_COOKIE_NAME)
            if not session_id:
                raise HTTPException(status_code=401, detail="not authenticated")
            user = auth.service.load_user_for_session(session, session_id)
            if user is None:
                raise HTTPException(status_code=401, detail="session expired")
            return user

        def require_human(request: Request, user: Any = Depends(require_user)) -> Any:
            """Refuse agent requests: the human-only hold-backs (accounts,
            passkeys, sessions and API tokens stay with a person)."""
            if auth.is_agent_request(request):
                raise HTTPException(status_code=403, detail=HUMAN_ONLY_DETAIL)
            return user

        def require_admin(user: Any = Depends(require_user)) -> Any:
            if auth.settings.AUTH_DISABLED:
                return user
            if not user.is_admin:
                raise HTTPException(status_code=403, detail="admin only")
            return user

        def require_human_admin(request: Request, user: Any = Depends(require_admin)) -> Any:
            if auth.is_agent_request(request):
                raise HTTPException(status_code=403, detail=HUMAN_ONLY_DETAIL)
            return user

        def require_actor(request: Request, user: Any = Depends(require_user)) -> str:
            """Who is acting, for audit rows: the user's name, marked when the
            request is an agent's (an API token or MCP)."""
            name = user.username or "user"
            return f"{name} (agent)" if auth.is_agent_request(request) else name

        self.get_db_session = get_db_session
        self.require_user = require_user
        self.require_human = require_human
        self.require_admin = require_admin
        self.require_human_admin = require_human_admin
        self.require_actor = require_actor
