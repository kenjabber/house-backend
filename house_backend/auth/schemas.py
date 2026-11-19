"""The request and response models of the ``/auth`` routes.

The fixed ones are plain classes. The user models (and the status model that
holds a user) are built per :class:`HouseAuth`, because an app's
:class:`UserExtension` adds fields to them. Every shared response field is
marked ``x-house: plain`` (D-16) and every timestamp leaves in UTC with its
zone (D-15).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, create_model

from house_backend.fields import Plain, UTCDateTime

if TYPE_CHECKING:
    from house_backend.auth.core import HouseAuth


# ── Requests ─────────────────────────────────────────────────────────────────


class RegisterBeginRequest(BaseModel):
    username: str
    display_name: str


class RegisterCompleteRequest(BaseModel):
    username: str
    display_name: str
    credential: dict[str, Any]


class LoginBeginRequest(BaseModel):
    username: str | None = None


class LoginCompleteRequest(BaseModel):
    credential: dict[str, Any]


class CredentialAddCompleteRequest(BaseModel):
    credential: dict[str, Any]
    nickname: str | None = None


class CredentialPatchRequest(BaseModel):
    nickname: str | None = None


class ApiTokenCreateRequest(BaseModel):
    name: str


class UserCreateBase(BaseModel):
    username: str
    display_name: str = ""
    is_admin: bool = False


class UserPatchBase(BaseModel):
    display_name: str | None = None
    is_admin: bool | None = None


# ── Responses ────────────────────────────────────────────────────────────────


class CredentialResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    credential_id: Plain[str]
    nickname: Plain[str | None]
    transports: Plain[str | None]
    aaguid: Plain[str | None]
    created: Plain[UTCDateTime]
    last_used_at: Plain[UTCDateTime | None]


class ApiTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Plain[int]
    name: Plain[str]
    prefix: Plain[str]
    created: Plain[UTCDateTime]
    last_used_at: Plain[UTCDateTime | None]


class ApiTokenCreated(ApiTokenResponse):
    # The plaintext token: returned by this one response only, never again.
    token: Plain[str]


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: Plain[str]
    created: Plain[UTCDateTime]
    expires_at: Plain[UTCDateTime]
    last_seen_at: Plain[UTCDateTime]
    user_agent: Plain[str | None]


class UserResponseBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Plain[str]
    username: Plain[str]
    display_name: Plain[str]
    is_admin: Plain[bool]


def _fields_of(model: type[BaseModel] | None) -> dict[str, Any]:
    """``create_model`` field definitions copied from ``model``."""
    if model is None:
        return {}
    return {name: (field.annotation, field) for name, field in model.model_fields.items()}


@dataclass(frozen=True)
class AuthSchemas:
    UserResponse: type[BaseModel]
    UserDetail: type[BaseModel]
    AuthStatusResponse: type[BaseModel]
    UserCreateRequest: type[BaseModel]
    UserPatchRequest: type[BaseModel]


def build_schemas(auth: HouseAuth) -> AuthSchemas:
    ext = auth.extension
    user_response = create_model("UserResponse", __base__=UserResponseBase, **_fields_of(ext.user_fields))
    user_detail = create_model(
        "UserDetail",
        __base__=user_response,
        has_passkey=(Plain[bool], ...),
        created=(Plain[UTCDateTime], ...),
        **_fields_of(ext.detail_fields),
    )
    status_fields: dict[str, Any] = {
        "bootstrapped": (Plain[bool], ...),
        "authenticated": (Plain[bool], ...),
    }
    if auth.multi_user:
        status_fields["site_name"] = (Plain[str], ...)
    status_fields["user"] = (Plain[user_response | None], None)
    status = create_model("AuthStatusResponse", **status_fields)
    create_request = create_model("UserCreateRequest", __base__=UserCreateBase, **_fields_of(ext.create_fields))
    patch_request = create_model("UserPatchRequest", __base__=UserPatchBase, **_fields_of(ext.patch_fields))
    return AuthSchemas(user_response, user_detail, status, create_request, patch_request)
