"""What an app adds to a user (PLAN D-12): its extra fields and columns.

An app with nothing to add uses :class:`UserExtension` as it is. An app that
adds something subclasses it::

    class UserExtra(BaseModel):
        household_id: int
        person_id: int | None

    class Households(UserExtension):
        user_fields = UserExtra            # on every user response (/me, /status, /users)

        def bootstrap_values(self, session):
            return {"household_id": first_household(session).id}

The models' fields carry their own ``x-house`` markers (or not): the shared
fields are marked plain (D-16), an app's are its own business.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session


class UserExtension:
    #: Fields added to every user response: ``/auth/me``, the ``user`` of
    #: ``/auth/status``, register and login, and the ``/auth/users`` routes.
    user_fields: type[BaseModel] | None = None
    #: Fields added to the admin's user detail only (``/auth/users`` routes).
    detail_fields: type[BaseModel] | None = None
    #: Fields ``POST /auth/users`` accepts besides username, display name and admin.
    create_fields: type[BaseModel] | None = None
    #: Fields ``PATCH /auth/users/{id}`` accepts besides display name and admin.
    patch_fields: type[BaseModel] | None = None

    def user_values(self, session: Session, user: Any) -> dict[str, Any]:
        """The values of :attr:`user_fields` for ``user``; by default its attributes."""
        return _attributes(self.user_fields, user)

    def detail_values(self, session: Session, user: Any) -> dict[str, Any]:
        """The values of :attr:`detail_fields` for ``user``; by default its attributes."""
        return _attributes(self.detail_fields, user)

    def new_user_values(self, session: Session, values: dict[str, Any]) -> dict[str, Any]:
        """The app's column values for an account an admin creates, from the
        :attr:`create_fields` of the request. Check them here and raise an
        ``HTTPException`` (422) for a bad one. By default they are the columns."""
        return values

    def update_user(self, session: Session, user: Any, values: dict[str, Any]) -> None:
        """Apply the :attr:`patch_fields` the request set (only those present).
        Check them here and raise an ``HTTPException`` (422) for a bad one. By
        default each is set as an attribute. The caller commits."""
        for name, value in values.items():
            setattr(user, name, value)

    def bootstrap_values(self, session: Session) -> dict[str, Any]:
        """Column values for a user row the library creates itself: the
        bootstrap admin (first registration) and the ``AUTH_DISABLED`` stand-in."""
        return {}


def _attributes(model: type[BaseModel] | None, user: Any) -> dict[str, Any]:
    if model is None:
        return {}
    return {name: getattr(user, name) for name in model.model_fields}
