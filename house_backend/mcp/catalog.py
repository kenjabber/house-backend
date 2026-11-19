"""What an app's MCP server exposes and, explicitly, what it does not.

Every REST operation the app serves is classified as exactly one of:

* **exposed**: an MCP tool calls it (``tool_operations``), unless it is
  human-only;
* **equivalent**: not called, because an exposed tool already provides the
  same capability (``equivalent``, operation → tool);
* **human-only**: deliberately held back from agents (``human_only``,
  operation → why). Those in ``human_only_enforced`` are also refused to a
  token at the REST layer (``require_human``).

The app keeps the data in its own ``catalog.py`` and builds a :class:`Catalog`
from it; :func:`house_backend.testing.check_operations_partition` asserts the
three sets partition the app's OpenAPI operations.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

# An operation is (HTTP method, OpenAPI path template).
Operation = tuple[str, str]

# The sign-in routes are human-only by nature but open to anyone, so they are
# not "enforced": a token is no use there, and they do not refuse one.
SIGN_IN_PREFIXES = ("/api/auth/register", "/api/auth/login", "/api/auth/logout")


class Catalog:
    def __init__(
        self,
        tool_operations: Mapping[str, tuple[Operation, ...]],
        equivalent: Mapping[Operation, str],
        human_only: Mapping[Operation, str],
        human_only_enforced: Iterable[Operation] | None = None,
    ):
        self.tool_operations = dict(tool_operations)
        self.equivalent = dict(equivalent)
        self.human_only = dict(human_only)
        if human_only_enforced is None:
            human_only_enforced = (op for op in self.human_only if not op[1].startswith(SIGN_IN_PREFIXES))
        self.human_only_enforced = frozenset(human_only_enforced)

    def exposed_operations(self) -> set[Operation]:
        """Every REST operation an MCP tool may call: those the tools name,
        less the human-only ones. A generic tool (a record tool reaching every
        operation of a resource) may name one that is human-only; it stays
        human-only, and the REST caller refuses it."""
        ops: set[Operation] = set()
        for tool_ops in self.tool_operations.values():
            ops.update(tool_ops)
        return ops - set(self.human_only)

    def tool_names(self) -> list[str]:
        """Every tool the server must register, in presentation order."""
        return list(self.tool_operations)
