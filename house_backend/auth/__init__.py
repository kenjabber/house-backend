"""Passkey login, sessions, API tokens and the ``/auth`` routes (PLAN D-9 to D-14).

See USAGE.md. The app declares its five tables on the mixins, builds one
:class:`HouseAuth`, re-exports its dependencies and includes ``auth.router()``.
"""

from house_backend.auth.core import HUMAN_ONLY_DETAIL, AuthModels, HouseAuth
from house_backend.auth.extension import UserExtension
from house_backend.auth.models import (
    BOOTSTRAP_USER_ID,
    ApiTokenMixin,
    UserMixin,
    UserSessionMixin,
    WebAuthnChallengeMixin,
    WebAuthnCredentialMixin,
)
from house_backend.auth.tokens import hash_token

__all__ = [
    "BOOTSTRAP_USER_ID",
    "HUMAN_ONLY_DETAIL",
    "ApiTokenMixin",
    "AuthModels",
    "HouseAuth",
    "UserExtension",
    "UserMixin",
    "UserSessionMixin",
    "WebAuthnChallengeMixin",
    "WebAuthnCredentialMixin",
    "hash_token",
]
