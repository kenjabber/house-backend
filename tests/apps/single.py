"""The single-user example app: one account, API tokens, no extras.

Shaped like an app that signs one person in (PLAN D-21).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from house_backend.auth import (
    ApiTokenMixin,
    AuthModels,
    HouseAuth,
    UserMixin,
    UserSessionMixin,
    WebAuthnChallengeMixin,
    WebAuthnCredentialMixin,
)
from house_backend.db import create_sqlite_engine
from house_backend.settings import HouseSettings


class Settings(HouseSettings):
    WEBAUTHN_RP_NAME: str = "single"
    WEBAUTHN_ORIGIN: str = "http://localhost:5999"
    SQLITE_PATH: str = str(Path.home() / ".local" / "share" / "house-backend-single" / "single.db")


settings = Settings(_env_file=None)


class Base(DeclarativeBase):
    pass


class User(UserMixin, Base):
    pass


class WebAuthnCredential(WebAuthnCredentialMixin, Base):
    pass


class UserSession(UserSessionMixin, Base):
    pass


class WebAuthnChallenge(WebAuthnChallengeMixin, Base):
    pass


class ApiToken(ApiTokenMixin, Base):
    pass


auth = HouseAuth(
    app_name="single",
    settings=settings,
    models=AuthModels(User, WebAuthnCredential, UserSession, WebAuthnChallenge, ApiToken),
)
require_user = auth.require_user


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


app = FastAPI(title="single", lifespan=lifespan)
app.include_router(auth.router(), prefix="/api")


@app.get("/api/status")
def status() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/whoami-actor")
def actor(name: str = Depends(auth.require_actor)) -> dict[str, str]:
    return {"actor": name}
