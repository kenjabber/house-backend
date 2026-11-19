"""The SQLite engine, consistent copies of a live database, and migration helpers.

Every app keeps its data in one SQLite file in WAL mode. Two rules follow:

* **One engine factory.** NullPool (a connection per session: SQLite gains
  nothing from a pool, and a pool runs dry when many sessions open at start-up),
  WAL, foreign keys on, and a busy timeout so writers queue instead of failing.
* **Never copy the file.** A file copy can miss what is still in the ``-wal``
  file or catch a page half-written. A copy is made with SQLite's online backup
  API, which folds the WAL in and yields one file that opens on its own.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import Connection, create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

SQLITE_LOCK_TIMEOUT_SECONDS = 30
SQLITE_BUSY_TIMEOUT_MS = SQLITE_LOCK_TIMEOUT_SECONDS * 1000
SQLITE_MAGIC = b"SQLite format 3\x00"


def create_sqlite_engine(path: str | os.PathLike[str]) -> Engine:
    """An engine on the SQLite file at ``path`` with the apps' pragmas.

    Each new connection gets ``journal_mode=WAL``, ``foreign_keys=ON`` (so
    ``ON DELETE CASCADE`` works) and ``busy_timeout``.
    """
    engine = create_engine(
        f"sqlite:///{path}",
        poolclass=NullPool,
        connect_args={"check_same_thread": False, "timeout": SQLITE_LOCK_TIMEOUT_SECONDS},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        cursor.close()

    return engine


# ── Consistent copies ────────────────────────────────────────────────────────


def snapshot(src: str | os.PathLike[str], dest: str | os.PathLike[str]) -> None:
    """Write a consistent copy of the database ``src`` to ``dest``.

    Uses the online backup API: what is committed (in the main file or still in
    the WAL) is in the copy, and the copy opens with no ``-wal`` file. ``dest``
    is replaced if it exists, and is made mode 600 (it holds what the database
    holds).
    """
    dest_path = Path(dest)
    if dest_path.exists():
        dest_path.unlink()
    source = sqlite3.connect(str(src))
    try:
        target = sqlite3.connect(str(dest_path))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    dest_path.chmod(0o600)


def backup_bytes(path: str | os.PathLike[str]) -> bytes:
    """A consistent copy of the database at ``path``, as the bytes of a ``.db`` file."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "backup.db"
        snapshot(path, copy)
        return copy.read_bytes()


class RestoreError(Exception):
    """The uploaded file is not a valid backup of this app's database."""


def restore_bytes(path: str | os.PathLike[str], data: bytes, required_tables: Iterable[str]) -> None:
    """Replace the contents of the live database at ``path`` with ``data``.

    ``data`` must be a SQLite file holding every table in ``required_tables``
    (a guard against restoring some other database); otherwise
    :class:`RestoreError` and nothing changes. The live database's WAL is
    checkpointed, then the upload's pages are copied over it with the backup
    API, so the same file and engine keep working: with NullPool the next
    session opens a fresh connection and sees the restored data. The file is
    made mode 600 afterwards.
    """
    if not data.startswith(SQLITE_MAGIC):
        raise RestoreError("not a SQLite database file")
    with tempfile.TemporaryDirectory() as tmp:
        upload = Path(tmp) / "upload.db"
        upload.write_bytes(data)
        source = sqlite3.connect(str(upload))
        try:
            try:
                names = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            except sqlite3.DatabaseError as exc:
                raise RestoreError(f"not a readable SQLite database: {exc}") from exc
            missing = set(required_tables) - names
            if missing:
                raise RestoreError(f"backup is missing tables: {sorted(missing)}")
            target = sqlite3.connect(str(path))
            try:
                target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
    Path(path).chmod(0o600)


# ── Migration helpers ────────────────────────────────────────────────────────


def table_columns(conn: Connection, table: str) -> set[str]:
    """The column names of ``table`` (empty if it does not exist)."""
    rows = conn.execute(text(f'PRAGMA table_info("{table}")')).fetchall()
    return {row[1] for row in rows}


def table_exists(conn: Connection, table: str) -> bool:
    row = conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": table},
    ).fetchone()
    return row is not None
