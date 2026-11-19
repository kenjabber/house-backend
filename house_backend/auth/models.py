"""The five auth tables, as mixins the app puts on its own ``Base`` (PLAN D-10).

::

    class User(UserMixin, Base):
        team_id: Mapped[int] = mapped_column(...)   # the app's own columns

The columns, types, lengths, nullability, defaults, indexes and ``ON DELETE
CASCADE`` are exactly the apps' existing ones, so adopting changes no table.
SQLite keeps naive UTC; the services write naive values.

The mixins' columns come first in each table and an app's own columns after
them, as in the apps' tables before house-backend.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

# The id of the first account: a placeholder row until the first registrant
# takes it over and becomes the admin.
BOOTSTRAP_USER_ID = "00000000-0000-0000-0000-000000000000"


# The mixins' columns come first and an app's own columns after them
# (SQLAlchemy would otherwise put a subclass's own columns first).
MIXIN_FIRST = -1


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _user_fk() -> Any:
    return mapped_column(
        String(36), ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True, sort_order=MIXIN_FIRST
    )


class UserMixin:
    """A registered web user. The first to register takes over the bootstrap
    placeholder and is the admin."""

    __tablename__ = "user"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, sort_order=MIXIN_FIRST)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, sort_order=MIXIN_FIRST)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, sort_order=MIXIN_FIRST)
    is_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0", sort_order=MIXIN_FIRST
    )
    created: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, sort_order=MIXIN_FIRST)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, sort_order=MIXIN_FIRST)


class WebAuthnCredentialMixin:
    """One registered passkey."""

    __tablename__ = "webauthn_credential"

    credential_id: Mapped[str] = mapped_column(String(255), primary_key=True, sort_order=MIXIN_FIRST)
    user_id: Mapped[str] = _user_fk()
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, sort_order=MIXIN_FIRST)
    sign_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0", sort_order=MIXIN_FIRST
    )
    transports: Mapped[str | None] = mapped_column(String(64), nullable=True, sort_order=MIXIN_FIRST)
    aaguid: Mapped[str | None] = mapped_column(String(36), nullable=True, sort_order=MIXIN_FIRST)
    nickname: Mapped[str | None] = mapped_column(String(120), nullable=True, sort_order=MIXIN_FIRST)
    created: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, sort_order=MIXIN_FIRST)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, sort_order=MIXIN_FIRST)


class UserSessionMixin:
    """A server-side opaque session (the cookie holds ``session_id``)."""

    __tablename__ = "user_session"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True, sort_order=MIXIN_FIRST)
    user_id: Mapped[str] = _user_fk()
    created: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, sort_order=MIXIN_FIRST)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, sort_order=MIXIN_FIRST)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, sort_order=MIXIN_FIRST)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True, sort_order=MIXIN_FIRST)


class WebAuthnChallengeMixin:
    """A pending registration or sign-in challenge: single use, short-lived."""

    __tablename__ = "webauthn_challenge"

    challenge: Mapped[str] = mapped_column(String(128), primary_key=True, sort_order=MIXIN_FIRST)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, sort_order=MIXIN_FIRST)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, sort_order=MIXIN_FIRST)
    created: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, sort_order=MIXIN_FIRST)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, sort_order=MIXIN_FIRST)


class ApiTokenMixin:
    """A bearer token for non-browser clients (the MCP server's credential).
    Only the SHA-256 of the token is stored."""

    __tablename__ = "api_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, sort_order=MIXIN_FIRST)
    user_id: Mapped[str] = _user_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False, sort_order=MIXIN_FIRST)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, sort_order=MIXIN_FIRST)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, sort_order=MIXIN_FIRST)
    created: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, sort_order=MIXIN_FIRST)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, sort_order=MIXIN_FIRST)
