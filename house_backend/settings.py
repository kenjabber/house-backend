"""``HouseSettings``: the settings the shared code reads (PLAN D-13).

Each app's ``Settings`` subclasses it, adds its own settings and overrides the
defaults that are its own (``WEBAUTHN_RP_NAME``, the dev port in
``WEBAUTHN_ORIGIN``, ``SITE_NAME``). The names are the apps' existing ones, so
no ``.env`` or Compose file changes. The database path is not here: the apps
name it differently, so :func:`house_backend.db.create_sqlite_engine` takes a
path.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


def _split(value: str) -> list[str]:
    return [s.strip() for s in value.split(",") if s.strip()]


class HouseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    # The name the app shows (on the sign-in page; ``/api/auth/status`` of a
    # multi-user app).
    SITE_NAME: str = ""
    # The running build, shown by apps that report one.
    BUILD_VERSION: str = "dev"

    # ── WebAuthn / session ────────────────────────────────────────────────
    WEBAUTHN_RP_ID: str = "localhost"
    WEBAUTHN_RP_NAME: str = ""
    # Comma-separated list of allowed origins (full scheme+host[:port]).
    WEBAUTHN_ORIGIN: str = "http://localhost:5173"
    SESSION_COOKIE_SECURE: bool = False
    SESSION_LIFETIME_DAYS: int = 30
    AUTH_RATE_LIMIT_PER_MINUTE: int = 20

    # DEVELOPMENT ONLY. When true, every request is treated as the bootstrap
    # admin and no passkey is required (a presented API token is still
    # checked). Never set it on a server.
    AUTH_DISABLED: bool = False

    # ── MCP ───────────────────────────────────────────────────────────────
    # /mcp only answers requests whose Host header is the WebAuthn RP ID or a
    # localhost name (DNS-rebinding protection). Comma-separated extra hosts.
    MCP_ALLOWED_HOSTS: str = ""

    @property
    def webauthn_allowed_origins(self) -> list[str]:
        return _split(self.WEBAUTHN_ORIGIN)

    @property
    def mcp_extra_hosts(self) -> list[str]:
        return _split(self.MCP_ALLOWED_HOSTS)
