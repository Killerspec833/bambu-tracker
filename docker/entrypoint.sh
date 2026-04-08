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

  # ── decide whether to enable MQTT ───────────────────────────────────────
  # A real config is mounted at /app/config.yaml.host when CONFIG_PATH is set.
  # /dev/null is the default mount, so check that the file has content.
  MQTT_FLAG="--no-mqtt"
  if [ -s /app/config.yaml.host ]; then
    echo "[entrypoint] Real config.yaml detected — copying and enabling MQTT."
    cp /app/config.yaml.host /app/config.yaml

    # Always rewrite token_file to the container-local mount path.
    # The token is mounted at /app/.bambu_token.host via TOKEN_FILE in .env.
    if [ -s /app/.bambu_token.host ]; then
      cp /app/.bambu_token.host /app/.bambu_token
      chmod 600 /app/.bambu_token
      echo "[entrypoint] Token file mounted and copied to /app/.bambu_token."
    else
      echo "[entrypoint] Warning: TOKEN_FILE not set or empty — MQTT will fail. Set TOKEN_FILE in .env."
    fi
    # Rewrite token_file in config regardless of whether the host file was
    # present — this ensures any tilde-expanded host path is replaced with
    # the container path so run.py always reads from /app/.bambu_token.
    python3 -c "
import re
path = '/app/config.yaml'
text = open(path).read()
text = re.sub(r'token_file:.*', 'token_file: \"/app/.bambu_token\"', text)
open(path, 'w').write(text)
print('[entrypoint] token_file path rewritten to /app/.bambu_token')
"

    MQTT_FLAG=""
  else
    echo "[entrypoint] No real config mounted (CONFIG_PATH not set) — MQTT disabled."
  fi

  echo "[entrypoint] Starting Bambu Tracker…"
  # When MQTT is disabled (web-only mode) use Gunicorn for the WSGI layer.
  # When MQTT is enabled, run.py manages the MQTT threads + a Werkzeug server;
  # Gunicorn's multi-worker fork model is incompatible with background threads.
  if [ -z "$MQTT_FLAG" ]; then
    exec python3 run.py
  else
    exec gunicorn wsgi:application \
      --workers "${GUNICORN_WORKERS:-2}" \
      --bind "0.0.0.0:7070" \
      --access-logfile - \
      --error-logfile -
  fi

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
