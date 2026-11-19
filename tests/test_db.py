"""house_backend.db: the engine's pragmas, snapshots, backup and restore."""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text

from house_backend.db import (
    RestoreError,
    backup_bytes,
    create_sqlite_engine,
    restore_bytes,
    snapshot,
    table_columns,
    table_exists,
)


@pytest.fixture
def db(tmp_path) -> Path:
    path = tmp_path / "app.db"
    engine = create_sqlite_engine(path)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE note (id INTEGER PRIMARY KEY, body TEXT)"))
        conn.execute(text("INSERT INTO note (body) VALUES ('first')"))
    engine.dispose()
    return path


def test_every_connection_gets_the_pragmas(db):
    engine = create_sqlite_engine(db)
    for _ in range(2):  # NullPool: each block is a new connection
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
            assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 30000
    engine.dispose()


def test_snapshot_holds_committed_rows_and_needs_no_wal(db, tmp_path):
    # A writer that never checkpoints: its committed row lives in the -wal file.
    writer = sqlite3.connect(db)
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("INSERT INTO note (body) VALUES ('committed, still in the WAL')")
    writer.commit()
    # A second connection holds an open, uncommitted write.
    pending = sqlite3.connect(db, timeout=0.1)
    pending.execute("BEGIN IMMEDIATE")
    pending.execute("INSERT INTO note (body) VALUES ('uncommitted')")
    assert Path(f"{db}-wal").stat().st_size > 0

    copy = tmp_path / "copy.db"
    snapshot(db, copy)
    pending.rollback()
    pending.close()
    writer.close()

    reader = sqlite3.connect(copy)
    bodies = [row[0] for row in reader.execute("SELECT body FROM note ORDER BY id")]
    reader.close()
    assert bodies == ["first", "committed, still in the WAL"]
    assert not Path(f"{copy}-wal").exists()
    assert copy.stat().st_mode & 0o777 == 0o600


def test_backup_and_restore_round_trip(db):
    saved = backup_bytes(db)
    engine = create_sqlite_engine(db)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM note"))
    restore_bytes(db, saved, required_tables={"note"})
    with engine.connect() as conn:  # the same engine sees the restored data
        assert conn.execute(text("SELECT body FROM note")).scalars().all() == ["first"]
    engine.dispose()
    assert db.stat().st_mode & 0o777 == 0o600


def test_restore_refuses_a_file_that_is_not_sqlite(db):
    with pytest.raises(RestoreError, match="not a SQLite"):
        restore_bytes(db, b"PK\x03\x04 a zip file", required_tables={"note"})


def test_restore_refuses_a_database_missing_a_required_table(db, tmp_path):
    other = tmp_path / "other.db"
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE note (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()
    with pytest.raises(RestoreError, match="missing tables: \\['user'\\]"):
        restore_bytes(db, other.read_bytes(), required_tables={"note", "user"})
    # Nothing changed.
    con = sqlite3.connect(db)
    assert con.execute("SELECT body FROM note").fetchall() == [("first",)]
    con.close()


def test_table_helpers(db):
    engine = create_sqlite_engine(db)
    with engine.connect() as conn:
        assert table_exists(conn, "note") and not table_exists(conn, "nothing")
        assert table_columns(conn, "note") == {"id", "body"}
        assert table_columns(conn, "nothing") == set()
    engine.dispose()


def test_snapshot_leaves_the_source_untouched(tmp_path):
    """A snapshot only reads: the source's size and modification time stay
    (from stocker's suite, where the start-up backup runs on the live file)."""
    src = tmp_path / "source.db"
    con = sqlite3.connect(src)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()
    before = src.stat()
    snapshot(src, tmp_path / "snapshot.db")
    after = src.stat()
    assert (after.st_size, after.st_mtime) == (before.st_size, before.st_mtime)
    assert (tmp_path / "snapshot.db").exists()
