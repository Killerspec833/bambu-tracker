#!/usr/bin/env bash
# Entrypoint for the Bambu Tracker container.
# CMD "run"  → migrate + start app
# CMD "test" → migrate test DB + run pytest
set -euo pipefail

# ── resolve config ─────────────────────────────────────────────────────────
# If no real config.yaml was mounted, fall back to the bundled docker sample.
if [ ! -f /app/config.yaml ]; then
  echo "[entrypoint] No config.yaml mounted — using docker/config.yaml.docker"
  cp /app/docker/config.yaml.docker /app/config.yaml
fi

CMD="${1:-run}"

# ── wait for postgres ──────────────────────────────────────────────────────
echo "[entrypoint] Waiting for database…"
until python3 -c "
import os, sys, time
try:
    import psycopg2
    url = os.environ.get('BAMBU_DB_URL') or ''
    psycopg2.connect(url).close()
    sys.exit(0)
except Exception as e:
    print(f'  not ready: {e}', flush=True)
    sys.exit(1)
" 2>&1; do
  sleep 1
done
echo "[entrypoint] Database is up."

if [ "$CMD" = "run" ]; then
  # ── migrate ──────────────────────────────────────────────────────────────
  echo "[entrypoint] Running alembic upgrade head…"
  alembic upgrade head

  # ── optional: bootstrap admin on first start ────────────────────────────
  # Set BAMBU_CREATE_ADMIN=1 + BAMBU_ADMIN_USER / BAMBU_ADMIN_EMAIL / BAMBU_ADMIN_PASS
  if [ "${BAMBU_CREATE_ADMIN:-0}" = "1" ]; then
    if [ -z "${BAMBU_ADMIN_PASS:-}" ]; then
      echo "[entrypoint] ERROR: BAMBU_CREATE_ADMIN=1 but BAMBU_ADMIN_PASS is not set. Refusing to create account with empty password."
      exit 1
    fi
    echo "[entrypoint] Creating admin user '${BAMBU_ADMIN_USER:-admin}'…"
    python3 - <<PYEOF
import os
from bambu_tracker.db import init_engine
from bambu_tracker.auth import create_user, get_user_by_username

init_engine(os.environ["BAMBU_DB_URL"])
username = os.environ.get("BAMBU_ADMIN_USER", "admin")
if not get_user_by_username(username):
    uid = create_user(
        username,
        os.environ.get("BAMBU_ADMIN_EMAIL", "admin@local"),
        os.environ["BAMBU_ADMIN_PASS"],
        role="admin",
    )
    print(f"Admin '{username}' created (id={uid})")
else:
    print(f"User '{username}' already exists — skipping")
PYEOF
  fi

  echo "[entrypoint] Starting Bambu Tracker…"
  exec python3 run.py --no-mqtt

elif [ "$CMD" = "test" ]; then
  echo "[entrypoint] Running smoke tests…"
  # Create the test DB if needed
  python3 - <<PYEOF
import os, psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import urllib.parse

url = os.environ.get("TEST_DB_URL", "")
parsed = urllib.parse.urlparse(url)
server_url = url.replace(parsed.path, "/postgres")
db_name = parsed.path.lstrip("/")

conn = psycopg2.connect(server_url)
conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
cur = conn.cursor()
cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
if not cur.fetchone():
    cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    print(f"Created test database: {db_name}")
else:
    print(f"Test database already exists: {db_name}")
conn.close()
PYEOF

  exec python3 -m pytest tests/ -v --tb=short

else
  echo "[entrypoint] Unknown CMD: $CMD"
  exit 1
fi
