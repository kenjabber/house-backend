"""Secret redaction for what an MCP server returns, by field name and by pattern.

The engine is shared; what is secret is the app's. An app builds one
:class:`Redactor` with

* ``secret_fields``: JSON field names whose (non-empty string) value is a secret;
* ``pattern``: a regular expression finding secrets in text (file content,
  diffs, logs). Each alternative has three named groups with a common prefix:
  ``<p>label``, ``<p>sep`` and ``<p>value`` (``label``/``sep``/``value``,
  ``blabel``/``bsep``/``bvalue``, …). The value is replaced, the label and
  separator are kept;
* ``placeholder_prefixes``: values that name a secret rather than hold one
  (``key-ref-…``) and pass through.

Empty values (``""``, ``''``, ``null``, ``~``) pass through, so "is one set?"
stays answerable. :func:`contains_redaction_marker` lets a write tool refuse a
payload that echoes the marker back (which would overwrite a real secret with
the marker).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

REDACTED = "<redacted>"
EMPTY_VALUES = frozenset({'""', "''", "null", "~"})


class Redactor:
    def __init__(
        self,
        secret_fields: Iterable[str],
        pattern: re.Pattern[str] | None = None,
        placeholder_prefixes: Iterable[str] = (),
        marker: str = REDACTED,
    ):
        self.secret_fields = frozenset(secret_fields)
        self.pattern = pattern
        self.placeholder_prefixes = tuple(placeholder_prefixes)
        self.marker = marker
        if pattern is not None:
            labels = [name for name in pattern.groupindex if name.endswith("label")]
            if not labels:
                raise ValueError("the pattern needs <p>label, <p>sep and <p>value groups")
            for label in labels:
                prefix = label.removesuffix("label")
                if f"{prefix}sep" not in pattern.groupindex or f"{prefix}value" not in pattern.groupindex:
                    raise ValueError(f"the pattern has {label} but not {prefix}sep and {prefix}value")
            self._prefixes = [label.removesuffix("label") for label in labels]

    def _replace(self, match: re.Match[str]) -> str:
        prefix = next(p for p in self._prefixes if match.group(f"{p}label") is not None)
        label, sep, value = (match.group(f"{prefix}{g}") for g in ("label", "sep", "value"))
        if value in EMPTY_VALUES:
            return match.group(0)  # "no secret set" is information, not a secret
        if self.placeholder_prefixes and value.strip("\"'").startswith(self.placeholder_prefixes):
            return match.group(0)  # a placeholder names its secret
        quote = value[0] if value[0] in "\"'" else ""
        # Keep anything the match spans before the label (a lookbehind needs none).
        head = match.string[match.start() : match.start(f"{prefix}label")]
        return f"{head}{label}{sep}{quote}{self.marker}{quote}"

    def redact_text(self, text: str) -> str:
        """Redact secrets from file content, diffs and logs."""
        return self.pattern.sub(self._replace, text) if self.pattern is not None else text

    def redact(self, value: Any) -> Any:
        """Recursively redact a JSON-shaped result: secret fields are replaced
        when set (``None`` and ``""`` stay), and every string goes through
        :meth:`redact_text`."""
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for key, item in value.items():
                if key in self.secret_fields and isinstance(item, str):
                    out[key] = self.marker if item else item
                else:
                    out[key] = self.redact(item)
            return out
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            return self.redact_text(value)
        return value

    def contains_marker(self, value: Any) -> bool:
        return contains_redaction_marker(value, self.marker)


def contains_redaction_marker(value: Any, marker: str = REDACTED) -> bool:
    """True if an inbound payload echoes the marker back."""
    if isinstance(value, dict):
        return any(contains_redaction_marker(v, marker) for v in value.values())
    if isinstance(value, list):
        return any(contains_redaction_marker(v, marker) for v in value)
    return isinstance(value, str) and marker in value
