"""
Pytest fixtures for Bambu Tracker integration smoke tests.

Requires a live Postgres instance. Set TEST_DB_URL before running:

    TEST_DB_URL=postgresql://bambu:bambu_dev_pass@localhost:5432/bambu_tracker_test pytest tests/ -v

When running via docker compose:
    docker compose --profile test run --rm test
"""
from __future__ import annotations

import os
import sys
from itertools import count

import pytest

# ── DB URL ────────────────────────────────────────────────────────────────────

TEST_DB_URL = os.environ.get(
    "TEST_DB_URL",
    "postgresql://bambu:bambu_dev_pass@localhost:5432/bambu_tracker_test",
)
_LOGIN_ADDR_COUNTER = count(10)


# ── create test database if it doesn't exist ──────────────────────────────────

def _ensure_test_db(url: str) -> None:
    import urllib.parse

    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    parsed = urllib.parse.urlparse(url)
    db_name = parsed.path.lstrip("/")
    server_url = url.replace(parsed.path, "/postgres")

    try:
        conn = psycopg2.connect(server_url)
    except Exception as exc:
        pytest.skip(f"Cannot connect to Postgres ({exc}). Set TEST_DB_URL to a reachable server.")
        return

    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
    if not cur.fetchone():
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    conn.close()


# ── session-scoped engine + schema ────────────────────────────────────────────

@pytest.fixture(scope="session")
def engine():
    _ensure_test_db(TEST_DB_URL)
    from bambu_tracker.db import init_engine, metadata

    eng = init_engine(TEST_DB_URL)

    # Drop and recreate all tables for a clean test run
    metadata.drop_all(eng)
    metadata.create_all(eng)

    yield eng

    # Leave tables in place for post-run inspection; CI pipelines drop the DB.


# ── inventory instance ────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def inv(engine):
    from bambu_tracker.inventory import Inventory
    return Inventory()


# ── seed admin user ───────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def admin_user(engine):
    from bambu_tracker.auth import create_user, get_user_by_username

    username = "testadmin"
    password = "S3cur3T3st!"
    existing = get_user_by_username(username)
    if existing:
        return {"id": existing.id, "username": username, "password": password}
    uid = create_user(username, "admin@test.local", password, role="admin")
    return {"id": uid, "username": username, "password": password}


# ── Flask app + test client ───────────────────────────────────────────────────

@pytest.fixture(scope="session")
def app(inv, engine):
    from bambu_tracker.web_ui import create_app

    flask_app = create_app(inv, {}, {}, secret_key="test-secret-key-for-pytest-only")
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    return flask_app


@pytest.fixture(scope="session")
def client(app):
    return app.test_client()


@pytest.fixture
def authed_client(client, admin_user):
    """Return a test client that is already logged in as the admin user."""
    resp = client.post(
        "/login",
        data={"username": admin_user["username"], "password": admin_user["password"]},
        follow_redirects=False,
        environ_overrides={"REMOTE_ADDR": f"127.0.0.{next(_LOGIN_ADDR_COUNTER)}"},
    )
    # Should redirect to dashboard (302) or already at dashboard (200)
    assert resp.status_code in (200, 302), f"Login failed: {resp.status_code}"
    yield client
    client.get("/logout", follow_redirects=False)
