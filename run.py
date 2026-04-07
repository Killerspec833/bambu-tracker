#!/usr/bin/env python3
"""
Entry point for Bambu Filament Tracker.

New in v2:
  - Postgres backend (configure database.url in config.yaml)
  - Multi-user auth (use --create-admin to create the first user)
  - Run DB migrations before starting: alembic upgrade head

Starts one MQTT listener thread per printer and the Flask web UI.
"""
from __future__ import annotations

import argparse
import getpass
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from bambu_tracker.alerts import AlertManager
from bambu_tracker.auth import create_user
from bambu_tracker.config import load_config, postgres_url, secret_key
from bambu_tracker.db import create_all_tables, init_engine
from bambu_tracker.inventory import Inventory
from bambu_tracker.models import Printer
from bambu_tracker.mqtt_client import BambuMQTTClient
from bambu_tracker.web_ui import create_app

logger = logging.getLogger("bambu_tracker")


def _bootstrap_admin(inv: Inventory) -> None:
    """Interactive prompt to create the first admin user."""
    print("\n── Create Admin User ──────────────────────────────")
    username = input("Username: ").strip()
    email = input("Email: ").strip()
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.")
        sys.exit(1)
    try:
        uid = create_user(username, email, password, role="admin")
        print(f"Admin user '{username}' created (id={uid}).")
    except Exception as exc:
        print(f"Error creating user: {exc}")
        sys.exit(1)


def main() -> None:
    # ─── args ─────────────────────────────────────────────────────────────────
    parser = argparse.ArgumentParser(description="Bambu Filament Tracker v2")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    parser.add_argument("--create-admin", action="store_true",
                        help="Interactively create an admin user then exit")
    parser.add_argument("--create-tables", action="store_true",
                        help="Create DB tables via create_all (dev only; use alembic in production)")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    parser.add_argument("--no-mqtt", action="store_true",
                        help="Skip MQTT client startup (web UI only; useful for docker dev)")
    args = parser.parse_args()

    if args.config is not None:
        config_path = args.config
    else:
        script_dir_cfg = Path(__file__).parent / "config.yaml"
        config_path = script_dir_cfg if script_dir_cfg.exists() else Path("config.yaml")

    # ─── config ───────────────────────────────────────────────────────────────
    config = load_config(config_path)
    alerts_cfg: dict[str, Any] = config.get("alerts", {})
    web_cfg: dict[str, Any] = config.get("web_ui", {})
    cloud_auth: dict[str, Any] = config.get("cloud_auth", {})
    cloud_username: str = cloud_auth.get("username", "")
    cloud_token_file: str = cloud_auth.get("token_file", "")

    # ─── database ─────────────────────────────────────────────────────────────
    db_url = postgres_url(config)
    logger.info("Database: %s", db_url.split("@")[-1])  # log host only, not creds
    init_engine(db_url)

    if args.create_tables:
        logger.info("Creating tables via create_all …")
        create_all_tables()
        logger.info("Tables created.")

    inv = Inventory()

    if args.create_admin:
        _bootstrap_admin(inv)
        sys.exit(0)

    # ─── upsert printers from config ──────────────────────────────────────────
    printers_cfg = config.get("printers", [])
    if not isinstance(printers_cfg, list) or not printers_cfg:
        logger.error("'printers' key is missing or empty in config.yaml.")
        sys.exit(1)

    printers: dict[str, Printer] = {}
    mqtt_clients: list[BambuMQTTClient] = []
    alert_manager = AlertManager(alerts_cfg)

    for idx, pcfg in enumerate(printers_cfg):
        try:
            name: str = pcfg["name"]
            serial: str = pcfg["serial"]
        except KeyError as exc:
            logger.error("Printer #%d missing field %s — skipping.", idx, exc)
            continue

        # Upsert into DB (creates printer_state row on first insert)
        try:
            printer_id = inv.upsert_printer(
                name=name,
                model=pcfg.get("model", ""),
                serial=serial,
                region=pcfg.get("region", "us"),
                cloud_username=cloud_username,
            )
        except Exception:
            logger.exception("Failed to upsert printer %r into DB — skipping.", name)
            continue

        printer = Printer(
            name=name,
            model=pcfg.get("model", ""),
            serial=serial,
        )
        printers[name] = printer

        def make_callbacks(p: Printer, p_id: int) -> tuple[Any, Any]:
            def on_job_start(pr: Printer) -> None:
                logger.info("[%s] Job started: %s", pr.name, pr.current_job)
                job_name = pr.current_job or "unknown"
                inv.start_job(pr.name, job_name)
                # upsert live state
                ams_data = [
                    {"index": s.index, "material": s.material,
                     "color": s.color, "remaining_pct": s.remaining_pct}
                    for s in pr.ams_slots
                ]
                inv.upsert_printer_state(pr.name, pr.state, pr.current_job, ams_data)

                if not alerts_cfg.get("pre_print_check", True):
                    return
                for slot in pr.ams_slots:
                    spool = inv.get_spool_by_printer_slot(pr.name, slot.index)
                    if spool is None:
                        continue
                    if spool.remaining_g <= spool.low_stock_threshold_g:
                        alert_manager.pre_print_insufficient(
                            printer=pr.name,
                            material=spool.material,
                            job=job_name,
                            needed_g=spool.low_stock_threshold_g,
                            remaining_g=spool.remaining_g,
                            slot=slot.index,
                        )
                        inv.create_alert(
                            "low_stock",
                            f"{spool.name}: only {spool.remaining_g:.0f}g left in slot {slot.index} before printing.",
                            spool_id=spool.id,
                            printer_id=p_id,
                        )

            def on_job_finish(pr: Printer) -> None:
                job_name = pr.current_job or "unknown"
                logger.info("[%s] Job finished: %s", pr.name, job_name)
                ams_data = [
                    {"index": s.index, "material": s.material,
                     "color": s.color, "remaining_pct": s.remaining_pct}
                    for s in pr.ams_slots
                ]
                inv.upsert_printer_state(pr.name, pr.state, pr.current_job, ams_data)

                filament_used: dict[int, float] = {}
                for slot in pr.ams_slots:
                    spool = inv.get_spool_by_printer_slot(pr.name, slot.index)
                    if spool is None:
                        continue
                    # Estimate filament used: current DB remaining minus what
                    # the AMS sensor reports.  The AMS percentage is relative to
                    # the spool's total weight, not its current remaining weight,
                    # so this is an approximation.  Clamp to [0, remaining_g] to
                    # avoid negative or impossibly large deductions.
                    ams_remaining_g = spool.total_weight_g * slot.remaining_pct / 100.0
                    grams_used = spool.remaining_g - ams_remaining_g
                    if grams_used < 0:
                        logger.warning(
                            "[%s] AMS slot %d reports more filament (%.0fg) than "
                            "DB remaining (%.0fg) — skipping deduction for this slot.",
                            pr.name, slot.index, ams_remaining_g, spool.remaining_g,
                        )
                        continue
                    grams_used = min(grams_used, spool.remaining_g)
                    if grams_used > 0:
                        filament_used[slot.index] = grams_used

                job_id = inv.log_print_job(
                    printer_name=pr.name,
                    subtask_name=job_name,
                    start_time=None,
                    end_time=None,
                    status=pr.state,
                    filament_used=filament_used,
                )

                for slot_idx, grams in filament_used.items():
                    updated_spool = inv.deduct_usage(
                        pr.name, slot_idx, grams,
                        note=f"Print job: {job_name}",
                        job_id=job_id,
                    )
                    if updated_spool is None:
                        continue
                    alert_manager.print_complete(
                        printer=pr.name,
                        job=job_name,
                        used_g=grams,
                        spool_name=updated_spool.name,
                        remaining_g=updated_spool.remaining_g,
                    )
                    if updated_spool.remaining_g <= 0:
                        alert_manager.spool_empty(updated_spool.name, pr.name, slot_idx)
                        inv.create_alert("low_stock",
                                         f"{updated_spool.name} is empty.",
                                         spool_id=updated_spool.id, printer_id=p_id)
                    elif updated_spool.remaining_g <= updated_spool.low_stock_threshold_g:
                        alert_manager.low_stock(
                            updated_spool.name, pr.name, slot_idx, updated_spool.remaining_g
                        )
                        inv.create_alert("low_stock",
                                         f"{updated_spool.name}: {updated_spool.remaining_g:.0f}g remaining.",
                                         spool_id=updated_spool.id, printer_id=p_id)

            return on_job_start, on_job_finish

        on_start, on_finish = make_callbacks(printer, printer_id)

        if not args.no_mqtt:
            client = BambuMQTTClient(
                printer_cfg=pcfg,
                printer_state=printer,
                cloud_username=cloud_username,
                cloud_token_file=cloud_token_file,
                on_job_start=on_start,
                on_job_finish=on_finish,
            )

            def _make_token_alert_cb(p_id: int) -> Any:
                def _on_token_expired(pname: str, message: str) -> None:
                    alert_manager._send(f"MQTT auth failed for {pname} — token expired", urgency="critical")
                    try:
                        inv.create_alert("printer_offline", message, printer_id=p_id)
                    except Exception:
                        logger.exception("Failed to create token-expiry alert in DB.")
                return _on_token_expired

            client.set_token_alert_callback(_make_token_alert_cb(printer_id))
            mqtt_clients.append(client)

    if not printers:
        logger.warning("No printers configured.")

    # ─── MQTT threads ─────────────────────────────────────────────────────────
    if args.no_mqtt:
        logger.info("--no-mqtt: skipping MQTT client startup.")
        started = 0
    else:
        started = 0
        for client in mqtt_clients:
            try:
                client.start()
                started += 1
            except Exception:
                logger.exception("Failed to start MQTT client.")
        logger.info("Started %d MQTT client(s).", started)

        def _push_all_after_delay() -> None:
            time.sleep(2)
            for c in mqtt_clients:
                try:
                    c.push_all()
                except Exception:
                    logger.exception("push_all failed.")

        threading.Thread(target=_push_all_after_delay, name="push-all-init", daemon=True).start()

    # ─── Flask ────────────────────────────────────────────────────────────────
    sk = secret_key(config)
    flask_app = create_app(inv, printers, config, secret_key=sk)
    host: str = web_cfg.get("host", "0.0.0.0")
    try:
        port: int = int(web_cfg.get("port", 7070))
    except ValueError:
        port = 7070

    from werkzeug.serving import make_server

    _http_server = None

    def _run_flask() -> None:
        nonlocal _http_server
        try:
            _http_server = make_server(host, port, flask_app)
            _http_server.serve_forever()
        except Exception:
            logger.exception("Flask web UI crashed.")

    flask_thread = threading.Thread(target=_run_flask, name="flask", daemon=True)
    flask_thread.start()
    logger.info("Web UI: http://%s:%d", host if host != "0.0.0.0" else "localhost", port)

    # ─── shutdown ─────────────────────────────────────────────────────────────
    stop_event = threading.Event()

    def _shutdown(signum: int, frame: object) -> None:
        logger.info("Shutting down …")
        for c in mqtt_clients:
            c.stop()
        if _http_server is not None:
            _http_server.shutdown()
        stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    stop_event.wait()
    logger.info("Goodbye.")
    sys.exit(0)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    main()
