"""The MCP plumbing (PLAN D-17), behind the ``house-backend[mcp]`` extra.

* :mod:`~house_backend.mcp.endpoint`: the ``/mcp`` route and its lifespan;
* :mod:`~house_backend.mcp.rest`: the in-process REST caller the tools use;
* :mod:`~house_backend.mcp.catalog`: which REST operations the tools expose;
* :mod:`~house_backend.mcp.redact`: the name- and pattern-based secret redactor.

The app keeps its tools, catalog data, ``INSTRUCTIONS`` and what it redacts.
"""
