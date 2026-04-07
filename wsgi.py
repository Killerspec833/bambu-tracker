"""WSGI entry point for production deployment.

Usage:
    gunicorn wsgi:application -w 4 -b 0.0.0.0:7070

Environment variables (see .env.example):
    BAMBU_CONFIG     Path to config.yaml (default: ./config.yaml)
    BAMBU_DB_URL     Overrides database.url in config.yaml
    BAMBU_SECRET_KEY Overrides web.secret_key in config.yaml
"""
from __future__ import annotations

import os
from pathlib import Path

from bambu_tracker.config import load_config, postgres_url, secret_key
from bambu_tracker.db import init_engine
from bambu_tracker.inventory import Inventory
from bambu_tracker.web_ui import create_app

_cfg_path = Path(os.environ.get("BAMBU_CONFIG", "config.yaml"))
config = load_config(_cfg_path)

_db_url = os.environ.get("BAMBU_DB_URL") or postgres_url(config)
init_engine(_db_url)

inv = Inventory()
_sk = os.environ.get("BAMBU_SECRET_KEY") or secret_key(config)
application = create_app(inv, {}, config, secret_key=_sk)
