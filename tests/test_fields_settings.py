"""house_backend.fields and house_backend.settings."""

from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated

from pydantic import BaseModel

from house_backend.fields import PLAIN, Plain, UTCDateTime, as_utc
from house_backend.settings import HouseSettings


class Stamp(BaseModel):
    at: UTCDateTime
    maybe: UTCDateTime | None = None


def test_a_naive_datetime_serialises_with_its_zone():
    data = Stamp(at=datetime(2026, 1, 2, 3, 4, 5)).model_dump(mode="json")
    assert data == {"at": "2026-01-02T03:04:05Z", "maybe": None}


def test_an_aware_datetime_is_converted_to_utc():
    plus_two = timezone(timedelta(hours=2))
    assert as_utc(datetime(2026, 1, 2, 5, 0, tzinfo=plus_two)) == datetime(2026, 1, 2, 3, 0, tzinfo=UTC)
    assert Stamp(at=datetime(2026, 1, 2, 3, 0), maybe=datetime(2026, 1, 2, 3, 0)).maybe.tzinfo is UTC


class Marked(BaseModel):
    name: Plain[str]
    note: Annotated[str | None, PLAIN] = None
    at: Plain[UTCDateTime]
    unmarked: int = 0


def test_the_plain_marker_is_in_the_json_schema():
    props = Marked.model_json_schema()["properties"]
    assert props["name"]["x-house"] == "plain"
    assert props["note"]["x-house"] == "plain"
    assert props["at"]["x-house"] == "plain"
    assert "x-house" not in props["unmarked"]
    assert Marked(name="n", at=datetime(2026, 1, 1)).model_dump(mode="json")["at"] == "2026-01-01T00:00:00Z"


def test_settings_defaults_and_lists(monkeypatch):
    monkeypatch.setenv("WEBAUTHN_ORIGIN", "https://app.example.test, http://localhost:5173")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "app.example.test,,")
    settings = HouseSettings(_env_file=None)
    assert settings.webauthn_allowed_origins == ["https://app.example.test", "http://localhost:5173"]
    assert settings.mcp_extra_hosts == ["app.example.test"]
    assert settings.AUTH_DISABLED is False and settings.SESSION_LIFETIME_DAYS == 30
    assert settings.AUTH_RATE_LIMIT_PER_MINUTE == 20 and settings.WEBAUTHN_RP_ID == "localhost"


def test_an_app_subclasses_and_overrides():
    class Settings(HouseSettings):
        WEBAUTHN_RP_NAME: str = "Example"
        SQLITE_PATH: str = "/tmp/example.db"

    settings = Settings(_env_file=None)
    assert settings.WEBAUTHN_RP_NAME == "Example" and settings.SQLITE_PATH.endswith("example.db")
