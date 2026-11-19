"""The auth mixins create exactly the tables the apps already have (PLAN D-9).

EXPECTED is the ``.schema`` of the five tables as the apps' own ``schema.py``
created them before house-backend, checked against each app on 2026-11-19. A
fresh database must match a deployed one, so a change here is a schema change
(a major version, D-5) and needs a migration in every app.
"""

import sqlite3

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from house_backend.auth import (
    ApiTokenMixin,
    UserMixin,
    UserSessionMixin,
    WebAuthnChallengeMixin,
    WebAuthnCredentialMixin,
)
from house_backend.db import create_sqlite_engine
from tests.apps import single

AUTH_TABLES = ("user", "webauthn_credential", "user_session", "webauthn_challenge", "api_token")

EXPECTED = """\
CREATE TABLE api_token (
    id INTEGER NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    name VARCHAR(120) NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    prefix VARCHAR(16) NOT NULL,
    created DATETIME NOT NULL,
    last_used_at DATETIME,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES user (id) ON DELETE CASCADE,
    UNIQUE (token_hash)
);
CREATE INDEX ix_api_token_user_id ON api_token (user_id);
CREATE INDEX ix_user_session_user_id ON user_session (user_id);
CREATE INDEX ix_webauthn_credential_user_id ON webauthn_credential (user_id);
CREATE TABLE user (
    id VARCHAR(36) NOT NULL,
    username VARCHAR(64) NOT NULL,
    display_name VARCHAR(120) NOT NULL,
    is_admin BOOLEAN DEFAULT '0' NOT NULL,
    created DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (username)
);
CREATE TABLE user_session (
    session_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    created DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    last_seen_at DATETIME NOT NULL,
    user_agent VARCHAR(255),
    PRIMARY KEY (session_id),
    FOREIGN KEY(user_id) REFERENCES user (id) ON DELETE CASCADE
);
CREATE TABLE webauthn_challenge (
    challenge VARCHAR(128) NOT NULL,
    user_id VARCHAR(36),
    purpose VARCHAR(16) NOT NULL,
    created DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    PRIMARY KEY (challenge)
);
CREATE TABLE webauthn_credential (
    credential_id VARCHAR(255) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    public_key BLOB NOT NULL,
    sign_count INTEGER DEFAULT '0' NOT NULL,
    transports VARCHAR(64),
    aaguid VARCHAR(36),
    nickname VARCHAR(120),
    created DATETIME NOT NULL,
    last_used_at DATETIME,
    PRIMARY KEY (credential_id),
    FOREIGN KEY(user_id) REFERENCES user (id) ON DELETE CASCADE
);
"""


def _schema(base, path) -> str:
    engine = create_sqlite_engine(path)
    base.metadata.create_all(engine)
    engine.dispose()
    con = sqlite3.connect(path)
    marks = ",".join("?" * len(AUTH_TABLES))
    rows = con.execute(
        f"SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND tbl_name IN ({marks}) ORDER BY name", AUTH_TABLES
    ).fetchall()
    con.close()
    text = "\n".join(sql + ";" for (sql,) in rows)
    # SQLite keeps the DDL as written: tabs, and a space before each newline.
    return "\n".join(line.rstrip().replace("\t", "    ") for line in text.splitlines())


def test_the_single_user_app_has_exactly_the_apps_tables(tmp_path):
    assert _schema(single.Base, tmp_path / "s.db") == EXPECTED.rstrip("\n")


def test_an_apps_own_columns_come_after_the_mixins(tmp_path):
    class Base(DeclarativeBase):
        pass

    class Crew(Base):
        __tablename__ = "crew"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)

    class User(UserMixin, Base):
        crew_id: Mapped[int] = mapped_column(Integer, ForeignKey("crew.id"), nullable=False)

    for mixin in (WebAuthnCredentialMixin, UserSessionMixin, WebAuthnChallengeMixin, ApiTokenMixin):
        type(mixin.__name__.removesuffix("Mixin"), (mixin, Base), {})

    engine = create_sqlite_engine(tmp_path / "m.db")
    Base.metadata.create_all(engine)
    engine.dispose()
    con = sqlite3.connect(tmp_path / "m.db")
    columns = [row[1] for row in con.execute("PRAGMA table_info(user)")]
    con.close()
    assert columns == ["id", "username", "display_name", "is_admin", "created", "updated_at", "crew_id"]
