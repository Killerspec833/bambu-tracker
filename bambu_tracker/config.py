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

    alerts = cfg.get("alerts", {})
    cfg.setdefault("alerts", {})
    cfg["alerts"].setdefault("desktop", True)
    cfg["alerts"].setdefault("openclaw", True)
    cfg["alerts"].setdefault("low_stock_grams", 50)
    cfg["alerts"].setdefault("pre_print_check", True)

    web_ui = cfg.get("web_ui", {})
    cfg.setdefault("web_ui", {})
    cfg["web_ui"].setdefault("port", 7070)
    cfg["web_ui"].setdefault("host", "0.0.0.0")


def mqtt_host_for_region(region: str) -> str:
    return MQTT_HOSTS.get(region, MQTT_HOSTS["us"])


def db_path() -> Path:
    """Return path to the SQLite database, ensuring the directory exists."""
    base = Path(os.environ.get("BAMBU_TRACKER_DB_DIR", Path.home() / ".bambu_tracker"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "inventory.db"
