"""The multi-user example app: accounts made by an admin, teams, API tokens.

Shaped like an app with many users that adds its own column to the user
(PLAN D-21): every user is in a team (a NOT NULL foreign key, so the bootstrap
admin needs one too), the user responses carry ``team_id``, the admin picks
the team when creating or editing an account, and the admin's user detail
carries a computed ``token_count``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from mcp.server.mcpserver import Context, MCPServer
from mcp_types import ToolAnnotations
from pydantic import BaseModel
from sqlalchemy import ForeignKey, Integer, String, func, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from house_backend.app import add_cors, mount_mcp, prepare_database
from house_backend.auth import (
    ApiTokenMixin,
    AuthModels,
    HouseAuth,
    UserExtension,
    UserMixin,
    UserSessionMixin,
    WebAuthnChallengeMixin,
    WebAuthnCredentialMixin,
)
from house_backend.db import table_columns
from house_backend.mcp.catalog import Catalog
from house_backend.mcp.endpoint import mcp_lifespan
from house_backend.mcp.rest import RestCaller
from house_backend.settings import HouseSettings


class Settings(HouseSettings):
    SITE_NAME: str = "Multi"
    WEBAUTHN_RP_NAME: str = "Multi"
    WEBAUTHN_ORIGIN: str = "http://localhost:5998"
    SQLITE_PATH: str = str(Path.home() / ".local" / "share" / "house-backend-multi" / "multi.db")


settings = Settings(_env_file=None)


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "team"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    note: Mapped[str | None] = mapped_column(String(120), nullable=True)


class User(UserMixin, Base):
    team_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("team.id", ondelete="RESTRICT"), nullable=False, index=True
    )


class WebAuthnCredential(WebAuthnCredentialMixin, Base):
    pass


class UserSession(UserSessionMixin, Base):
    pass


class WebAuthnChallenge(WebAuthnChallengeMixin, Base):
    pass


class ApiToken(ApiTokenMixin, Base):
    pass


def first_team(session: Session) -> Team:
    team = session.query(Team).order_by(Team.id).first()
    if team is None:
        team = Team(name="Team")
        session.add(team)
        session.commit()
    return team


class UserTeam(BaseModel):
    team_id: int


class UserTokens(BaseModel):
    token_count: int


class UserTeamChange(BaseModel):
    team_id: int | None = None


class Teams(UserExtension):
    user_fields = UserTeam
    detail_fields = UserTokens
    create_fields = UserTeam
    patch_fields = UserTeamChange

    def detail_values(self, session: Session, user: Any) -> dict[str, Any]:
        count = session.scalar(select(func.count()).select_from(ApiToken).where(ApiToken.user_id == user.id))
        return {"token_count": count}

    def new_user_values(self, session: Session, values: dict[str, Any]) -> dict[str, Any]:
        if session.get(Team, values["team_id"]) is None:
            raise HTTPException(status_code=422, detail="team_id: no such team")
        return values

    def update_user(self, session: Session, user: Any, values: dict[str, Any]) -> None:
        if "team_id" in values:
            if values["team_id"] is None or session.get(Team, values["team_id"]) is None:
                raise HTTPException(status_code=422, detail="team_id: no such team")
            user.team_id = values["team_id"]

    def bootstrap_values(self, session: Session) -> dict[str, Any]:
        return {"team_id": first_team(session).id}


auth = HouseAuth(
    app_name="multi",
    settings=settings,
    models=AuthModels(User, WebAuthnCredential, UserSession, WebAuthnChallenge, ApiToken),
    multi_user=True,
    api_tokens=True,
    extension=Teams(),
)
require_user = auth.require_user
get_db_session = auth.get_db_session


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.warn_if_auth_disabled()
    engine = prepare_database(app, settings.SQLITE_PATH, Base, migrate=migrate)
    async with mcp_lifespan(app, build_server, settings):
        try:
            yield
        finally:
            engine.dispose()


def migrate(engine) -> None:
    """The app's migrations: here, one that adds a column old databases lack."""
    with engine.begin() as conn:
        if "note" not in table_columns(conn, "team"):
            conn.execute(text("ALTER TABLE team ADD COLUMN note VARCHAR(120)"))


app = FastAPI(title="multi", lifespan=lifespan)
add_cors(app, settings)
app.include_router(auth.router(), prefix="/api")


@app.get("/api/status")
def status() -> dict[str, str]:
    return {"status": "ok"}


class TeamOut(BaseModel):
    id: int
    name: str


@app.get("/api/teams", response_model=list[TeamOut])
def list_teams(_user: Any = Depends(require_user), session: Session = Depends(get_db_session)) -> Any:
    return [TeamOut(id=t.id, name=t.name) for t in session.query(Team).order_by(Team.id)]


class TeamIn(BaseModel):
    name: str


@app.post("/api/teams", response_model=TeamOut, status_code=201)
def create_team(
    body: TeamIn, _admin: Any = Depends(auth.require_admin), session: Session = Depends(get_db_session)
) -> Any:
    team = Team(name=body.name.strip())
    session.add(team)
    session.commit()
    return TeamOut(id=team.id, name=team.name)


@app.put("/api/teams/{team_id}/badge")
async def put_badge(team_id: int, request: Request, _admin: Any = Depends(auth.require_admin)) -> dict[str, int]:
    """A raw upload (the request body is the file), as file routes take."""
    return {"team_id": team_id, "bytes": len(await request.body())}


# ── MCP: one tool ────────────────────────────────────────────────────────────

_ACCOUNTS = "accounts, passkeys, sessions and API tokens are managed by a person"

CATALOG = Catalog(
    tool_operations={"list_teams": (("GET", "/api/teams"),)},
    equivalent={
        ("GET", "/api/status"): "list_teams",
        ("GET", "/api/auth/status"): "list_teams",
        ("GET", "/api/auth/me"): "list_teams",
        ("GET", "/api/auth/users"): "list_teams",
        ("POST", "/api/teams"): "list_teams",
        ("PUT", "/api/teams/{team_id}/badge"): "list_teams",
    },
    human_only={
        (method, f"/api/auth/{path}"): _ACCOUNTS
        for method, path in [
            ("POST", "register/begin"),
            ("POST", "register/complete"),
            ("POST", "login/begin"),
            ("POST", "login/complete"),
            ("POST", "logout"),
            ("POST", "users"),
            ("PATCH", "users/{user_id}"),
            ("DELETE", "users/{user_id}"),
            ("POST", "users/{user_id}/reset-passkeys"),
            ("GET", "credentials"),
            ("POST", "credentials/begin"),
            ("POST", "credentials/complete"),
            ("PATCH", "credentials/{credential_id}"),
            ("DELETE", "credentials/{credential_id}"),
            ("GET", "sessions"),
            ("DELETE", "sessions/{session_id}"),
            ("GET", "tokens"),
            ("POST", "tokens"),
            ("DELETE", "tokens/{token_id}"),
        ]
    },
)


def _redact_team_names(app: Any, method: str, template: str, data: Any) -> Any:
    """The example's redaction hook: a team called "secret" is not shown."""
    if template == "/api/teams":
        return [t if t["name"] != "secret" else {**t, "name": "<redacted>"} for t in data]
    return data


rest = RestCaller(auth, CATALOG, redact=_redact_team_names)


def build_server() -> MCPServer:
    server = MCPServer(name="multi", instructions="The multi-user example app.")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    async def list_teams(ctx: Context) -> dict[str, Any]:
        """Every team, with its id."""
        request = ctx.request_context.request
        teams = await rest.call(request.app, request.headers.get("authorization"), "GET", "/api/teams")
        return {"teams": teams}

    return server


mount_mcp(app, auth, "MCP requires an API token (Settings → Tokens)")
