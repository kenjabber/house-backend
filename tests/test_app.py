"""house_backend.app (start-up helpers) and the parity checks' own failures."""

import json
import sqlite3
import stat
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from house_backend.app import add_cors, build_info, prepare_database
from house_backend.mcp.catalog import Catalog
from house_backend.settings import HouseSettings
from house_backend.testing import check_operations_partition


class Base(DeclarativeBase):
    pass


class Thing(Base):
    __tablename__ = "thing"
    id: Mapped[int] = mapped_column(primary_key=True)


def test_prepare_database(tmp_path):
    app = FastAPI()
    path = tmp_path / "data" / "app.db"
    ran = []
    engine = prepare_database(app, path, Base, migrate=ran.append)
    assert ran == [engine] and app.state.engine is engine
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with app.state.SessionFactory() as session:
        assert session.query(Thing).count() == 0
    engine.dispose()


def test_the_example_migration_runs_at_start_up(tmp_path, monkeypatch):
    from tests.apps import multi

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE team (id INTEGER PRIMARY KEY, name VARCHAR(64) NOT NULL UNIQUE)")
    con.commit()
    con.close()
    monkeypatch.setattr(multi.settings, "SQLITE_PATH", str(path))
    with TestClient(multi.app):
        pass
    con = sqlite3.connect(path)
    assert "note" in [row[1] for row in con.execute("PRAGMA table_info(team)")]
    con.close()


def test_add_cors_allows_the_webauthn_origins():
    app = FastAPI()
    add_cors(app, HouseSettings(_env_file=None, WEBAUTHN_ORIGIN="https://app.example.test"))

    @app.get("/x")
    def x():
        return {}

    client = TestClient(app)
    ok = client.get("/x", headers={"origin": "https://app.example.test"})
    assert ok.headers["access-control-allow-origin"] == "https://app.example.test"
    assert ok.headers["access-control-allow-credentials"] == "true"
    assert (
        "access-control-allow-origin" not in client.get("/x", headers={"origin": "https://other.example.test"}).headers
    )


def test_build_info_from_an_archive(tmp_path):
    build = tmp_path / "archive" / "build.json"
    build.parent.mkdir()
    build.write_text(json.dumps({"commit": "a" * 40, "committed_at": "2026-01-01T00:00:00+00:00"}))
    info = build_info(build)
    assert info["source"] == "archive" and info["short"] == "a" * 12 and info["dirty"] is False
    assert info["started_at"]


def test_build_info_from_git_and_unknown(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "build.json").write_text('{"commit": "$Format:%H$"}')
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.test"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "x"], check=True)
    info = build_info(repo / "build.json")
    assert info["source"] == "git" and len(info["commit"]) == 40 and info["dirty"] is False
    (tmp_path / "plain").mkdir()
    assert build_info(tmp_path / "plain" / "build.json")["source"] in ("unknown", "git")


def test_the_partition_check_names_what_is_wrong():
    app = FastAPI()

    @app.get("/api/a")
    def a():
        return {}

    @app.get("/api/b")
    def b():
        return {}

    with pytest.raises(AssertionError, match="unclassified.*/api/b"):
        check_operations_partition(app, Catalog({"t": (("GET", "/api/a"),)}, {}, {}))
    with pytest.raises(AssertionError, match="no longer serves.*/api/c"):
        check_operations_partition(
            app, Catalog({"t": (("GET", "/api/a"), ("GET", "/api/b"), ("GET", "/api/c"))}, {}, {})
        )
    with pytest.raises(AssertionError, match="classified twice"):
        check_operations_partition(
            app, Catalog({"t": (("GET", "/api/a"), ("GET", "/api/b"))}, {("GET", "/api/a"): "t"}, {})
        )
    with pytest.raises(AssertionError, match="tools that do not exist"):
        check_operations_partition(app, Catalog({"t": (("GET", "/api/a"),)}, {("GET", "/api/b"): "nope"}, {}))


def test_build_info_is_read_once_until_the_cache_is_cleared(tmp_path):
    build = tmp_path / "b" / "build.json"
    build.parent.mkdir()
    build.write_text(json.dumps({"commit": "a" * 40}))
    assert build_info(build)["commit"] == "a" * 40
    build.write_text(json.dumps({"commit": "b" * 40}))
    assert build_info(build)["commit"] == "a" * 40
    build_info.cache_clear()
    assert build_info(build)["commit"] == "b" * 40


def test_the_client_fixture_serves_an_app_without_a_lifespan(pytester):
    """An app of the routers alone (no lifespan) gets a SessionFactory on the
    test database from the shared ``client`` fixture."""
    pytester.makeini("[pytest]\nasyncio_mode = auto\nasyncio_default_fixture_loop_scope = function\n")
    pytester.makeconftest(
        """
import pytest
from fastapi import FastAPI
from tests_apps_single import single

pytest_plugins = ["house_backend.testing"]
bare = FastAPI()
bare.include_router(single.auth.router(), prefix="/api")

@pytest.fixture
def house_app():
    return bare

@pytest.fixture
def house_auth():
    return single.auth

@pytest.fixture
def house_base():
    return single.Base
"""
    )
    pytester.makepyfile(
        """
def test_bare(auth_client):
    assert auth_client.get("/api/auth/me").json()["username"] == "alex"


def test_bare_member(member_client):
    assert member_client.get("/api/auth/me").json()["username"] == "sam"
"""
    )
    import shutil
    from pathlib import Path

    shutil.copytree(Path(__file__).parent / "apps", pytester.path / "tests_apps_single")
    pytester.syspathinsert()
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=2)
