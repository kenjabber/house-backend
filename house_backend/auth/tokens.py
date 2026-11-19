"""API tokens: the bearer credential for non-browser clients (MCP).

A token is ``<app>_`` + 32 random URL-safe bytes. Only its SHA-256 is stored
(``token_hash``); the plaintext is returned once, by :meth:`TokenService.create_token`.
A high-entropy random token needs no salt or slow hash (a plain digest cannot
be brute-forced), so checking one stays a single indexed lookup.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from house_backend.auth.core import HouseAuth

# ``last_used_at`` is informational; do not turn every request into a write.
LAST_USED_RESOLUTION = timedelta(minutes=1)


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenService:
    def __init__(self, auth: HouseAuth):
        self.auth = auth
        self.prefix = f"{auth.app_name}_"

    @property
    def _model(self) -> Any:
        model = self.auth.models.ApiToken
        if model is None:
            raise RuntimeError("API tokens are off for this app (HouseAuth(api_tokens=False))")
        return model

    def create_token(self, session: Session, *, user_id: str, name: str) -> tuple[Any, str]:
        """Create a token; returns the row and the plaintext (never retrievable again)."""
        plaintext = self.prefix + secrets.token_urlsafe(32)
        row = self._model(
            user_id=user_id,
            name=name,
            token_hash=hash_token(plaintext),
            prefix=plaintext[: len(self.prefix) + 4],
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row, plaintext

    def list_tokens(self, session: Session, user_id: str) -> list[Any]:
        model = self._model
        return session.query(model).filter(model.user_id == user_id).order_by(model.created.asc(), model.id.asc()).all()

    def user_for_token(self, session: Session, token: str) -> Any | None:
        """The user a presented bearer token belongs to, or ``None`` if unknown."""
        model = self._model
        row = session.query(model).filter(model.token_hash == hash_token(token)).first()
        if row is None:
            return None
        user = session.get(self.auth.models.User, row.user_id)
        if user is None:
            return None
        now = _now()
        if row.last_used_at is None or now - row.last_used_at >= LAST_USED_RESOLUTION:
            row.last_used_at = now
            session.commit()
        return user
