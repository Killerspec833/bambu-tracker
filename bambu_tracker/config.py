from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = Path("config.yaml")
EXAMPLE_CONFIG_PATH = Path("config.yaml.example")

# MQTT broker hostnames per region
MQTT_HOSTS: dict[str, str] = {
    "us": "us.mqtt.bambulab.com",
    "eu": "eu.mqtt.bambulab.com",
    "ap": "ap.mqtt.bambulab.com",
}
MQTT_PORT = 8883


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load and validate config.yaml. Exits with a clear message if missing."""
    if not path.exists():
        print(
            f"ERROR: config.yaml not found at '{path.resolve()}'.\n"
            f"Copy {EXAMPLE_CONFIG_PATH} to config.yaml and fill in your printer credentials.",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(path, "r") as fh:
        raw = yaml.safe_load(fh)

    if not raw:
        print("ERROR: config.yaml is empty.", file=sys.stderr)
        sys.exit(1)

    _validate_config(raw)
    return raw


def _validate_config(cfg: dict[str, Any]) -> None:
    """Raise ValueError for obviously invalid config."""
    if "printers" not in cfg or not isinstance(cfg["printers"], list):
        raise ValueError("config.yaml: 'printers' must be a non-empty list.")

    for i, printer in enumerate(cfg["printers"]):
        for key in ("name", "serial", "access_token", "region"):
            if key not in printer:
                raise ValueError(f"config.yaml: printers[{i}] missing '{key}'.")
        if printer["region"] not in MQTT_HOSTS:
            raise ValueError(
                f"config.yaml: printers[{i}] region '{printer['region']}' "
                f"must be one of: {list(MQTT_HOSTS.keys())}"
            )

    if not isinstance(cfg.get("alerts"), dict):
        cfg["alerts"] = {}
    cfg["alerts"].setdefault("desktop", True)
    cfg["alerts"].setdefault("openclaw", True)
    cfg["alerts"].setdefault("low_stock_grams", 50)
    cfg["alerts"].setdefault("pre_print_check", True)

    if not isinstance(cfg.get("web_ui"), dict):
        cfg["web_ui"] = {}
    cfg["web_ui"].setdefault("port", 7070)
    cfg["web_ui"].setdefault("host", "0.0.0.0")
    cfg["web_ui"].setdefault("secret_key", "change-me-in-production")


def mqtt_host_for_region(region: str) -> str:
    return MQTT_HOSTS.get(region, MQTT_HOSTS["us"])


def db_path() -> Path:
    """Return path to the SQLite database, ensuring the directory exists.
    Kept for backwards compatibility; new code uses postgres_url()."""
    base = Path(os.environ.get("BAMBU_TRACKER_DB_DIR", Path.home() / ".bambu_tracker"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "inventory.db"


def postgres_url(cfg: dict[str, Any]) -> str:
    """Return Postgres URL from config or BAMBU_DB_URL env override."""
    env_url = os.environ.get("BAMBU_DB_URL")
    if env_url:
        return env_url
    db_cfg = cfg.get("database", {})
    url = db_cfg.get("url", "")
    if not url:
        raise ValueError(
            "No database.url found in config.yaml and BAMBU_DB_URL env var is not set.\n"
            "Add 'database: url: postgresql://user:pass@host/dbname' to config.yaml."
        )
    return url


def secret_key(cfg: dict[str, Any]) -> str:
    """Return Flask secret key from config or BAMBU_SECRET_KEY env override."""
    env_key = os.environ.get("BAMBU_SECRET_KEY")
    if env_key:
        return env_key
    web_cfg = cfg.get("web_ui", {})
    key = web_cfg.get("secret_key", "")
    if not key or key == "change-me-in-production":
        import secrets as _sec
        import logging as _log
        _log.getLogger(__name__).warning(
            "web_ui.secret_key is not set in config.yaml — using a random key. "
            "Sessions will not survive restarts."
        )
        return _sec.token_hex(32)
    return key
