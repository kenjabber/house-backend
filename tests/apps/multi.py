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

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import ForeignKey, Integer, String, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

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
from house_backend.db import create_sqlite_engine
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
    db_path = Path(settings.SQLITE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(db_path)
    Base.metadata.create_all(engine)
    db_path.chmod(0o600)
    app.state.engine = engine
    app.state.SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        yield
    finally:
        engine.dispose()


app = FastAPI(title="multi", lifespan=lifespan)
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
