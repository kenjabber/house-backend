"""Passkeys (WebAuthn), sessions, challenges and accounts.

Wraps the ``webauthn`` library so the router stays about HTTP: issuing and
consuming challenges, verifying attestations and assertions, creating and
deleting opaque server-side sessions, the bootstrap take-over, and the
multi-user account helpers.

SQLite keeps naive UTC, so every value written here is naive.
"""

from __future__ import annotations

import base64
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from house_backend.auth.models import BOOTSTRAP_USER_ID

if TYPE_CHECKING:
    from house_backend.auth.core import HouseAuth

CHALLENGE_TTL_MINUTES = 5


def now() -> datetime:
    return datetime.now(tz=UTC)


def naive(dt: datetime) -> datetime:
    """SQLite keeps naive UTC; align a value of either shape with it."""
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _options_to_dict(options: Any) -> dict[str, Any]:
    """py-webauthn options as the JSON the browser's WebAuthn API takes
    (binary fields base64url-encoded)."""
    return json.loads(options_to_json(options))


def _challenge_from_client_data(response: dict[str, Any]) -> str:
    raw_client = response.get("response", {}).get("clientDataJSON")
    if not raw_client:
        raise ValueError("response missing clientDataJSON")
    challenge = json.loads(b64url_decode(raw_client).decode("utf-8")).get("challenge")
    if not challenge:
        raise ValueError("clientDataJSON missing challenge")
    return challenge


class PasskeyService:
    def __init__(self, auth: HouseAuth):
        self.auth = auth

    @property
    def _m(self):
        return self.auth.models

    @property
    def _settings(self):
        return self.auth.settings

    # ── Lookups ──────────────────────────────────────────────────────────────

    def is_bootstrapped(self, session: Session) -> bool:
        """True once the first real user has registered a passkey.

        Checked by the existence of any credential, not by user id: the first
        admin keeps the bootstrap id after the take-over, so an id-based check
        would stay False and let anyone overwrite that account.
        """
        return session.query(self._m.WebAuthnCredential).first() is not None

    def get_user_by_username(self, session: Session, username: str) -> Any | None:
        """The user called ``username``. Multi-user apps lower-case new
        usernames, but an older account may have been stored as typed
        (``Ken``): with ``multi_user``, a name with no exact match finds the
        one account that matches it ignoring case (none if several do)."""
        model = self._m.User
        user = session.query(model).filter(model.username == username).first()
        if user is None and self.auth.multi_user:
            matches = session.query(model).filter(func.lower(model.username) == username.lower()).limit(2).all()
            user = matches[0] if len(matches) == 1 else None
        return user

    def list_user_credentials(self, session: Session, user_id: str) -> list[Any]:
        cred = self._m.WebAuthnCredential
        return session.query(cred).filter(cred.user_id == user_id).order_by(cred.created.asc()).all()

    def user_has_credentials(self, session: Session, user_id: str) -> bool:
        cred = self._m.WebAuthnCredential
        return session.query(cred).filter(cred.user_id == user_id).first() is not None

    # ── Challenges ───────────────────────────────────────────────────────────

    def _store_challenge(self, session: Session, *, challenge: bytes, user_id: str | None, purpose: str) -> None:
        session.add(
            self._m.WebAuthnChallenge(
                challenge=b64url(challenge),
                user_id=user_id,
                purpose=purpose,
                created=naive(now()),
                expires_at=naive(now() + timedelta(minutes=CHALLENGE_TTL_MINUTES)),
            )
        )
        session.commit()

    def consume_challenge(self, session: Session, *, challenge_b64: str, purpose: str) -> Any | None:
        """Look up, check and delete a stored challenge (single use).
        ``None`` if unknown, for another purpose, or expired."""
        row = session.get(self._m.WebAuthnChallenge, challenge_b64)
        if row is None or row.purpose != purpose:
            return None
        expired = naive(row.expires_at) <= naive(now())
        session.delete(row)
        session.commit()
        return None if expired else row

    def purge_expired_challenges(self, session: Session) -> int:
        model = self._m.WebAuthnChallenge
        deleted = session.query(model).filter(model.expires_at <= naive(now())).delete(synchronize_session=False)
        # Always commit after a DML statement: SQLite opens a write transaction
        # on any DELETE, even one that matched nothing, and holding it stalls
        # other writers.
        session.commit()
        return deleted

    # ── Registration and sign-in ─────────────────────────────────────────────

    def begin_registration(
        self,
        session: Session,
        *,
        username: str,
        display_name: str,
        user_id: str,
        exclude_credential_ids: list[str],
    ) -> dict[str, Any]:
        self.purge_expired_challenges(session)
        options = generate_registration_options(
            rp_id=self._settings.WEBAUTHN_RP_ID,
            rp_name=self._settings.WEBAUTHN_RP_NAME,
            user_id=user_id.encode("utf-8"),
            user_name=username,
            user_display_name=display_name,
            exclude_credentials=[PublicKeyCredentialDescriptor(id=b64url_decode(c)) for c in exclude_credential_ids],
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
            supported_pub_key_algs=[
                COSEAlgorithmIdentifier.ECDSA_SHA_256,
                COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
            ],
        )
        self._store_challenge(session, challenge=options.challenge, user_id=user_id, purpose="register")
        return _options_to_dict(options)

    def complete_registration(self, session: Session, *, user_id: str, response: dict[str, Any]) -> Any:
        """Verify an attestation for ``user_id`` and store the new passkey.
        ``ValueError`` if the challenge or the attestation is not valid."""
        challenge_b64 = _challenge_from_client_data(response)
        row = self.consume_challenge(session, challenge_b64=challenge_b64, purpose="register")
        if row is None or row.user_id != user_id:
            raise ValueError("registration challenge invalid or expired")

        verification = verify_registration_response(
            credential=response,
            expected_challenge=b64url_decode(challenge_b64),
            expected_rp_id=self._settings.WEBAUTHN_RP_ID,
            expected_origin=self._settings.webauthn_allowed_origins,
            require_user_verification=False,
        )
        cred = self._m.WebAuthnCredential(
            credential_id=b64url(verification.credential_id),
            user_id=user_id,
            public_key=verification.credential_public_key,
            sign_count=verification.sign_count,
            transports=",".join(response.get("response", {}).get("transports", []) or []) or None,
            aaguid=str(verification.aaguid) if getattr(verification, "aaguid", None) else None,
            nickname=None,
            created=naive(now()),
        )
        session.add(cred)
        session.commit()
        return cred

    def begin_authentication(self, session: Session, *, user: Any | None) -> dict[str, Any]:
        self.purge_expired_challenges(session)
        allow = []
        if user is not None:
            allow = [
                PublicKeyCredentialDescriptor(id=b64url_decode(c.credential_id))
                for c in self.list_user_credentials(session, user.id)
            ]
        options = generate_authentication_options(
            rp_id=self._settings.WEBAUTHN_RP_ID,
            allow_credentials=allow or None,
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        self._store_challenge(
            session, challenge=options.challenge, user_id=user.id if user else None, purpose="authenticate"
        )
        return _options_to_dict(options)

    def complete_authentication(self, session: Session, *, response: dict[str, Any]) -> Any:
        """Verify an assertion; returns the user. ``ValueError`` if not valid."""
        challenge_b64 = _challenge_from_client_data(response)
        challenge = self.consume_challenge(session, challenge_b64=challenge_b64, purpose="authenticate")
        if challenge is None:
            raise ValueError("authentication challenge invalid or expired")

        raw_credential_id = response.get("rawId") or response.get("id")
        if not raw_credential_id:
            raise ValueError("authentication response missing credential id")
        cred = session.get(self._m.WebAuthnCredential, raw_credential_id)
        if cred is None:
            raise ValueError("unknown credential")
        if challenge.user_id is not None and challenge.user_id != cred.user_id:
            raise ValueError("credential does not match challenge user")

        verification = verify_authentication_response(
            credential=response,
            expected_challenge=b64url_decode(challenge_b64),
            expected_rp_id=self._settings.WEBAUTHN_RP_ID,
            expected_origin=self._settings.webauthn_allowed_origins,
            credential_public_key=cred.public_key,
            credential_current_sign_count=cred.sign_count,
            require_user_verification=False,
        )
        # The counter must advance, unless the authenticator keeps none (both 0).
        if verification.new_sign_count <= cred.sign_count and not (
            verification.new_sign_count == 0 and cred.sign_count == 0
        ):
            raise ValueError("authenticator sign_count did not advance — possible replay")

        cred.sign_count = verification.new_sign_count
        cred.last_used_at = naive(now())
        session.commit()
        user = session.get(self._m.User, cred.user_id)
        if user is None:
            raise ValueError("user vanished")
        return user

    # ── Sessions ─────────────────────────────────────────────────────────────

    def create_session(self, session: Session, *, user_id: str, user_agent: str | None) -> Any:
        row = self._m.UserSession(
            session_id=secrets.token_urlsafe(32),
            user_id=user_id,
            created=naive(now()),
            expires_at=naive(now() + timedelta(days=self._settings.SESSION_LIFETIME_DAYS)),
            last_seen_at=naive(now()),
            user_agent=(user_agent or "")[:255] or None,
        )
        session.add(row)
        session.commit()
        return row

    def delete_session(self, session: Session, session_id: str) -> bool:
        row = session.get(self._m.UserSession, session_id)
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True

    def purge_expired_sessions(self, session: Session) -> int:
        model = self._m.UserSession
        deleted = session.query(model).filter(model.expires_at <= naive(now())).delete(synchronize_session=False)
        session.commit()
        return deleted

    def list_user_sessions(self, session: Session, user_id: str) -> list[Any]:
        self.purge_expired_sessions(session)
        model = self._m.UserSession
        return (
            session.query(model)
            .filter(model.user_id == user_id)
            .filter(model.expires_at > naive(now()))
            .order_by(model.last_seen_at.desc())
            .all()
        )

    def load_user_for_session(self, session: Session, session_id: str) -> Any | None:
        """The user of a session cookie, or ``None``. An expired session is
        deleted when it is presented."""
        row = session.get(self._m.UserSession, session_id)
        if row is None:
            return None
        if naive(row.expires_at) <= naive(now()):
            session.delete(row)
            session.commit()
            return None
        user = session.get(self._m.User, row.user_id)
        if user is None:
            return None
        row.last_seen_at = naive(now())
        session.commit()
        return user

    # ── Accounts ─────────────────────────────────────────────────────────────

    def take_over_bootstrap_user(self, session: Session, *, username: str, display_name: str) -> Any:
        """Turn the bootstrap placeholder into the first real user, in place:
        the id stays :data:`BOOTSTRAP_USER_ID` and the user is the admin."""
        user = session.get(self._m.User, BOOTSTRAP_USER_ID)
        if user is None:
            user = self._m.User(
                id=BOOTSTRAP_USER_ID,
                username=username,
                display_name=display_name,
                is_admin=True,
                created=naive(now()),
                updated_at=naive(now()),
                **self.auth.extension.bootstrap_values(session),
            )
            session.add(user)
        else:
            user.username = username
            user.display_name = display_name
            user.is_admin = True
            user.updated_at = naive(now())
        session.commit()
        return user

    def dev_bypass_user(self, session: Session) -> Any:
        """The stand-in admin under ``AUTH_DISABLED`` (development only): the
        bootstrap row, else the oldest user, else a new bootstrap row, so a
        wiped dev database still has a user id for foreign keys."""
        model = self._m.User
        user = session.get(model, BOOTSTRAP_USER_ID)
        if user is None:
            user = session.query(model).order_by(model.created.asc()).first()
        if user is None:
            user = model(
                id=BOOTSTRAP_USER_ID,
                username="bootstrap",
                display_name="bootstrap",
                is_admin=True,
                **self.auth.extension.bootstrap_values(session),
            )
            session.add(user)
            session.commit()
        return user

    def list_all_users(self, session: Session) -> list[Any]:
        model = self._m.User
        return session.query(model).order_by(model.created.asc()).all()

    def create_user(self, session: Session, *, username: str, display_name: str, is_admin: bool, **values: Any) -> Any:
        """An account without a passkey yet: its owner registers one at
        ``/register``. ``values`` are the app's own columns."""
        user = self._m.User(
            id=str(uuid.uuid4()),
            username=username,
            display_name=display_name,
            is_admin=is_admin,
            created=naive(now()),
            updated_at=naive(now()),
            **values,
        )
        session.add(user)
        session.commit()
        return user

    def delete_user_credentials(self, session: Session, user_id: str) -> int:
        """Remove every passkey of a user (so they can register a new one)."""
        model = self._m.WebAuthnCredential
        deleted = session.query(model).filter(model.user_id == user_id).delete(synchronize_session=False)
        session.commit()
        return deleted
