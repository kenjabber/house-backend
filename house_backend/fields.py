"""Field types for response models: UTC timestamps and the ``x-house`` marker.

* :data:`UTCDateTime` (PLAN D-15). SQLite keeps naive UTC; a response gives
  the zone back, so a client never guesses (it serialises as ``…Z``).
* :data:`PLAIN` and :data:`Plain` (D-16). A field marked ``x-house: plain`` in
  its JSON schema holds nothing an app needs to withhold from an agent. An app
  that withholds every field it cannot classify (NestEgg) accepts these by
  rule. Every field of the shared response models carries it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import AfterValidator, Field

X_HOUSE = "x-house"


def as_utc(value: datetime) -> datetime:
    """A naive value is UTC (SQLite has no time zone); an aware one is converted."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


# A datetime that leaves the API timezone-aware, in UTC.
UTCDateTime = Annotated[datetime, AfterValidator(as_utc)]

# ``Field`` metadata that marks a field ``x-house: plain``:
# ``name: Annotated[str, PLAIN]``.
PLAIN: Any = Field(json_schema_extra={X_HOUSE: "plain"})


class _PlainAlias:
    """``Plain[T]`` is ``Annotated[T, PLAIN]``."""

    def __getitem__(self, item: Any) -> Any:
        return Annotated[item, PLAIN]


Plain = _PlainAlias()


def house_marker(property_schema: dict[str, Any]) -> str | None:
    """The ``x-house`` value of one property of a JSON schema, or ``None``."""
    return property_schema.get(X_HOUSE)
