#!/usr/bin/env python3
"""
Entry point for Bambu Filament Tracker.
Starts one MQTT listener thread per printer and the Flask web UI.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from bambu_tracker.alerts import AlertManager
from bambu_tracker.config import db_path, load_config
from bambu_tracker.inventory import Inventory
from bambu_tracker.models import Printer
from bambu_tracker.mqtt_client import BambuMQTTClient
from bambu_tracker.web_ui import create_app

logger = logging.getLogger("bambu_tracker")


def main() -> None:
    # ------------------------------------------------------------------ args
    parser = argparse.ArgumentParser(description="Bambu Filament Tracker")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (default: next to this script, then CWD)",
    )
    args = parser.parse_args()

    if args.config is not None:
        config_path = args.config
    else:
        script_dir_cfg = Path(__file__).parent / "config.yaml"
        config_path = script_dir_cfg if script_dir_cfg.exists() else Path("config.yaml")

    # ------------------------------------------------------------------ config
    config = load_config(config_path)
    alerts_cfg: dict[str, Any] = config.get("alerts", {})
    web_cfg: dict[str, Any] = config.get("web_ui", {})
    cloud_auth: dict[str, Any] = config.get("cloud_auth", {})
    cloud_username: str = cloud_auth.get("username", "")
    cloud_token_file: str = cloud_auth.get("token_file", "")

    alert_manager = AlertManager(alerts_cfg)
    inv = Inventory(db_path())

    logger.info("Database: %s", db_path())

    # ---------------------------------------------------------------- printers
    printers_cfg = config.get("printers", [])
    if not isinstance(printers_cfg, list) or not printers_cfg:
        logger.error(
            "Configuration error: 'printers' key is missing or empty in config.yaml. "
            "Please add at least one printer entry."
        )
        sys.exit(1)

    printers: dict[str, Printer] = {}
    mqtt_clients: list[BambuMQTTClient] = []

    for idx, pcfg in enumerate(printers_cfg):
        try:
            name: str = pcfg["name"]
            serial: str = pcfg["serial"]
        except KeyError as exc:
            logger.error(
                "Printer entry #%d is missing required field %s — skipping.",
                idx, exc,
            )
            continue

        if name in printers:
            logger.warning(
                "Duplicate printer name %r at entry #%d — overwriting previous entry.",
                name, idx,
            )

        printer = Printer(
            name=name,
            model=pcfg.get("model", ""),
            serial=serial,
        )
        printers[name] = printer

        def make_callbacks(p: Printer) -> tuple[Any, Any]:
            def on_job_start(pr: Printer) -> None:
                logger.info("[%s] Job started: %s", pr.name, pr.current_job)
                job_name = pr.current_job or "unknown"
                inv.start_job(pr.name, job_name)
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

            def on_job_finish(pr: Printer) -> None:
                job_name = pr.current_job or "unknown"
                logger.info("[%s] Job finished: %s", pr.name, job_name)

                # Estimate filament used per slot from AMS remaining_pct vs inventory
                filament_used: dict[int, float] = {}
                for slot in pr.ams_slots:
                    spool = inv.get_spool_by_printer_slot(pr.name, slot.index)
                    if spool is None:
                        continue
                    ams_remaining_g = spool.total_weight_g * slot.remaining_pct / 100.0
                    grams_used = max(0.0, spool.remaining_g - ams_remaining_g)
                    if grams_used > 0:
                        filament_used[slot.index] = grams_used

                inv.log_print_job(
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
                    elif updated_spool.remaining_g <= updated_spool.low_stock_threshold_g:
                        alert_manager.low_stock(
                            updated_spool.name, pr.name, slot_idx, updated_spool.remaining_g
                        )

            return on_job_start, on_job_finish

        on_start, on_finish = make_callbacks(printer)

        client = BambuMQTTClient(
            printer_cfg=pcfg,
            printer_state=printer,
            cloud_username=cloud_username,
            cloud_token_file=cloud_token_file,
            on_job_start=on_start,
            on_job_finish=on_finish,
        )
        mqtt_clients.append(client)

    if not printers:
        logger.warning("No printers configured.")

    # ------------------------------------------------------------------ MQTT threads
    started = 0
    for client in mqtt_clients:
        try:
            client.start()
            started += 1
        except Exception:
            logger.exception("Failed to start MQTT client — skipping this printer.")
    logger.info("Started %d MQTT client(s).", started)

    def _push_all_after_delay() -> None:
        time.sleep(2)
        for c in mqtt_clients:
            try:
                c.push_all()
            except Exception:
                logger.exception("push_all failed for a client.")

    threading.Thread(target=_push_all_after_delay, name="push-all-init", daemon=True).start()

    # ------------------------------------------------------------------ Flask
    # NOTE: flask_app.run() is for development only.
    # For production use gunicorn: gunicorn "bambu_tracker.web_ui:create_app()"
    flask_app = create_app(inv, printers, config)
    host: str = web_cfg.get("host", "0.0.0.0")
    try:
        port: int = int(web_cfg.get("port", 7070))
    except ValueError:
        logger.error(
            "Invalid port value %r in web_ui config — falling back to 7070.",
            web_cfg.get("port"),
        )
        port = 7070

    def _run_flask() -> None:
        try:
            flask_app.run(host=host, port=port, use_reloader=False)
        except Exception:
            logger.exception("Flask web UI crashed unexpectedly.")

    flask_thread = threading.Thread(target=_run_flask, name="flask", daemon=True)
    flask_thread.start()
    logger.info("Web UI: http://%s:%d", host if host != "0.0.0.0" else "localhost", port)

    # ------------------------------------------------------------------ shutdown
    stop_event = threading.Event()

    def _shutdown(signum: int, frame: object) -> None:
        logger.info("Shutting down …")
        for c in mqtt_clients:
            c.stop()
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
