"""Start-up helpers an app's lifespan and ``main.py`` call (PLAN D-17, D-18).

Not a function that builds the whole app: ``main.py`` stays a readable list
of routers and start-up steps, with the repeated steps here::

    @asynccontextmanager
    async def lifespan(app):
        auth.warn_if_auth_disabled(logger)
        prepare_database(app, settings.SQLITE_PATH, Base, migrate=run_sqlite_migrations)
        async with mcp_lifespan(app, build_server, settings):
            yield

    app = FastAPI(title="example", lifespan=lifespan)
    add_cors(app, settings)
    app.include_router(auth.router(), prefix="/api")
    mount_mcp(app, auth, "MCP requires an API token (Settings → API tokens)")
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from house_backend.db import create_sqlite_engine
from house_backend.settings import HouseSettings


def prepare_database(
    app: FastAPI,
    path: str | os.PathLike[str],
    base: Any,
    migrate: Callable[[Engine], None] | None = None,
) -> Engine:
    """Make the data directory, open the engine, create the missing tables,
    run the app's migrations, make the file mode 600, and put ``engine`` and
    ``SessionFactory`` on ``app.state``. Returns the engine."""
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(db_path)
    base.metadata.create_all(engine)
    if migrate is not None:
        migrate(engine)
    if db_path.exists():
        db_path.chmod(0o600)
    app.state.engine = engine
    app.state.SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine


def add_cors(app: FastAPI, settings: HouseSettings) -> None:
    """CORS for the WebAuthn origins, with credentials (the dev frontend runs
    on another port)."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.webauthn_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def mount_mcp(app: FastAPI, auth: Any, unauthorized_detail: str = "MCP requires an API token") -> None:
    """Add the raw ``/mcp`` route (the MCP SDK owns the wire protocol). The
    lifespan must run :func:`house_backend.mcp.endpoint.mcp_lifespan`."""
    from starlette.routing import Route

    from house_backend.mcp.endpoint import MCP_PATH, McpEndpoint

    app.router.routes.append(Route(MCP_PATH, endpoint=McpEndpoint(auth, unauthorized_detail)))


# ── Build identity ───────────────────────────────────────────────────────────

STARTED_AT = datetime.now(UTC).isoformat(timespec="seconds")


def _git(directory: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _from_file(build_file: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(build_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    commit = data.get("commit") if isinstance(data, dict) else None
    if not isinstance(commit, str) or "$Format" in commit:
        return None  # a checkout, not an archive
    return {"commit": commit, "committed_at": data.get("committed_at"), "dirty": False, "source": "archive"}


def _from_git(directory: Path) -> dict[str, Any] | None:
    commit = _git(directory, "rev-parse", "HEAD")
    if not commit:
        return None
    status = _git(directory, "status", "--porcelain", "--untracked-files=no")
    return {
        "commit": commit,
        "committed_at": _git(directory, "log", "-1", "--format=%cI", "HEAD"),
        "dirty": None if status is None else status != "",
        "source": "git",
    }


@cache
def _identity(build_file: Path) -> dict[str, Any]:
    found = _from_file(build_file) or _from_git(build_file.parent)
    return found or {"commit": None, "committed_at": None, "dirty": None, "source": "unknown"}


def build_info(build_file: str | os.PathLike[str]) -> dict[str, Any]:
    """Which build is running: the commit the backend was built from.

    A deploy that exports the commit with ``git archive`` fills the
    ``$Format:…$`` placeholders of ``build_file`` (``export-subst`` in
    ``.gitattributes``)::

        {"commit": "$Format:%H$", "committed_at": "$Format:%cI$"}

    so the image carries its own commit with no ``.git``. In a checkout the
    placeholders stay as they are and the answer comes from git in the file's
    directory, including whether the tree is dirty.

    Returns ``commit`` (full SHA or None), ``short``, ``committed_at``,
    ``dirty`` (True only for a checkout with uncommitted changes to tracked
    files), ``source`` (``archive``, ``git`` or ``unknown``) and
    ``started_at``, when this process started. Read once per process
    (``build_info.cache_clear()`` forgets it, for tests)."""
    found = _identity(Path(build_file).resolve())
    commit = found["commit"]
    return {**found, "short": commit[:12] if commit else None, "started_at": STARTED_AT}


# The identity is read once per process; a test that rewrites the file clears it.
build_info.cache_clear = _identity.cache_clear  # type: ignore[attr-defined]
