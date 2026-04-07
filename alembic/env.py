from __future__ import annotations

"""
Alembic env.py — reads the DB URL from config.yaml (or BAMBU_DB_URL env var)
so alembic upgrade/downgrade works without hardcoding credentials.

Usage:
  alembic upgrade head
  alembic revision --autogenerate -m "add foo"
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ── project root on sys.path ──────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bambu_tracker.db import metadata  # noqa: E402

# ── Alembic Config ────────────────────────────────────────────────────────────
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def _get_url() -> str:
    env_url = os.environ.get("BAMBU_DB_URL")
    if env_url:
        return env_url
    cfg_path = ROOT / "config.yaml"
    if cfg_path.exists():
        import yaml
        with open(cfg_path) as fh:
            raw = yaml.safe_load(fh) or {}
        url = raw.get("database", {}).get("url", "")
        if url:
            return url
    raise RuntimeError(
        "No database URL found. Set BAMBU_DB_URL env var or add "
        "'database: url: postgresql://...' to config.yaml."
    )


# ── offline migrations (--sql mode) ──────────────────────────────────────────

def run_migrations_offline() -> None:
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── online migrations ─────────────────────────────────────────────────────────

def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
