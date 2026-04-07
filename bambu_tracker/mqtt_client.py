from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

import paho.mqtt.client as mqtt

from .config import mqtt_host_for_region, MQTT_PORT
from .models import AmsSlot, Printer

logger = logging.getLogger(__name__)


class BambuMQTTClient:
    """Manages a persistent MQTT connection to a single Bambu Cloud printer."""

    def __init__(
        self,
        printer_cfg: dict[str, Any],
        printer_state: Printer,
        cloud_username: str = "",
        cloud_token_file: str = "",
        on_job_start: Callable[[Printer], None] | None = None,
        on_job_finish: Callable[[Printer], None] | None = None,
    ) -> None:
        for key in ("name", "serial", "region"):
            if key not in printer_cfg:
                raise ValueError(f"printer_cfg missing required key: {key!r}")

        if not cloud_username:
            raise ValueError("cloud_username must not be empty")

        self._name: str = printer_cfg["name"]
        self._serial: str = printer_cfg["serial"]
        try:
            self._host: str = mqtt_host_for_region(printer_cfg["region"])
        except Exception as exc:
            raise ValueError(
                f"[{printer_cfg['name']}] Unknown region: {printer_cfg['region']!r}"
            ) from exc
        self._topic: str = f"device/{self._serial}/report"
        self._request_topic: str = f"device/{self._serial}/request"
        self._cloud_username: str = cloud_username
        self._cloud_token_file: str = cloud_token_file

        self._printer = printer_state
        self._on_job_start = on_job_start
        self._on_job_finish = on_job_finish
        self._client: mqtt.Client | None = None
        self._client_lock = threading.Lock()
        self._started = False
        self._stop_event = threading.Event()
        # Token staleness: track last successful connect time to detect expiry
        self._last_connected_at: datetime | None = None
        self._token_alert_cb: Callable[[str, str], None] | None = None

    def _read_token(self) -> str:
        if not self._cloud_token_file:
            raise RuntimeError("cloud_token_file is required but was not set")
        path = os.path.expanduser(self._cloud_token_file)
        try:
            with open(path, "r") as fh:
                token = fh.read().strip()
        except FileNotFoundError:
            raise RuntimeError(f"Token file not found: {path!r}")
        except PermissionError:
            raise RuntimeError(f"Permission denied reading token file: {path!r}")
        if not token:
            raise RuntimeError(f"Token file is empty: {path!r}")
        return token

    def _build_client(self) -> mqtt.Client:
        client = mqtt.Client(
            client_id=f"bambu-tracker-{self._serial}",
            protocol=mqtt.MQTTv311,
        )
        token = self._read_token()
        client.username_pw_set(username=self._cloud_username, password=token)

        # Bambu Lab cloud MQTT uses a self-signed certificate; hostname checking
        # and certificate verification are intentionally disabled.  This is the
        # documented approach for the Bambu MQTT API.
        tls_ctx = ssl.create_default_context()
        tls_ctx.check_hostname = False
        tls_ctx.verify_mode = ssl.CERT_NONE
        client.tls_set_context(tls_ctx)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        return client

    # ---------------------------------------------------------------- MQTT callbacks

    def set_token_alert_callback(self, cb: Callable[[str, str], None]) -> None:
        """Register a callback(printer_name, message) fired when the token looks stale."""
        self._token_alert_cb = cb

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: dict[str, Any],
        rc: int,
    ) -> None:
        try:
            if rc == 0:
                logger.info("[%s] Connected to Bambu Cloud MQTT.", self._name)
                self._last_connected_at = datetime.now(timezone.utc)
                result, _ = client.subscribe(self._topic, qos=0)
                if result != mqtt.MQTT_ERR_SUCCESS:
                    logger.warning(
                        "[%s] subscribe() failed, rc=%d.", self._name, result
                    )
            elif rc == 4:
                # rc=4 → bad credentials; most likely an expired token
                msg = (
                    f"[{self._name}] MQTT auth failed (rc=4) — "
                    "Bambu Cloud token is likely expired. "
                    f"Refresh {self._cloud_token_file!r} and restart."
                )
                logger.error(msg)
                if self._token_alert_cb:
                    try:
                        self._token_alert_cb(self._name, msg)
                    except Exception:
                        pass
            else:
                logger.warning("[%s] MQTT connect failed, rc=%d.", self._name, rc)
        except Exception as exc:
            logger.error("[%s] Error in _on_connect: %s", self._name, exc)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        rc: int,
    ) -> None:
        try:
            if rc != 0 and not self._stop_event.is_set():
                logger.warning(
                    "[%s] Disconnected unexpectedly (rc=%d).", self._name, rc
                )
        except Exception as exc:
            logger.error("[%s] Error in _on_disconnect: %s", self._name, exc)

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        try:
            try:
                payload = json.loads(msg.payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.debug("[%s] Unreadable message: %s", self._name, exc)
                return

            logger.debug("[%s] MQTT payload keys: %s", self._name, list(payload.keys()))
            self._parse_print_state(payload)
        except Exception as exc:
            logger.error("[%s] Error in _on_message: %s", self._name, exc)

    def _parse_print_state(self, payload: dict[str, Any]) -> None:
        """Update self._printer from a Bambu MQTT report payload."""
        print_data = payload.get("print")
        if not isinstance(print_data, dict):
            return

        prev_state = self._printer.state

        # --- gcode_state → printer state ---
        new_state = print_data.get("gcode_state")
        if new_state and isinstance(new_state, str):
            self._printer.state = new_state.upper()
            logger.info("[%s] State: %s", self._name, self._printer.state)

        # --- subtask_name → current_job ---
        subtask = print_data.get("subtask_name")
        if subtask is not None:
            self._printer.current_job = subtask or None

        # --- AMS slots ---
        ams_root = print_data.get("ams")
        if isinstance(ams_root, dict):
            ams_units = ams_root.get("ams")
            if isinstance(ams_units, list):
                slots: list[AmsSlot] = []
                for unit in ams_units:
                    if not isinstance(unit, dict):
                        continue
                    unit_id = int(unit.get("id", 0))
                    for tray in unit.get("tray", []):
                        if not isinstance(tray, dict):
                            continue
                        tray_id = int(tray.get("id", 0))
                        slot_index = unit_id * 4 + tray_id
                        raw_color = tray.get("tray_color", "")
                        color = f"#{raw_color}" if raw_color and not raw_color.startswith("#") else raw_color
                        slots.append(AmsSlot(
                            index=slot_index,
                            material=tray.get("tray_type", ""),
                            color=color,
                            remaining_pct=int(tray.get("remain", 0)),
                        ))
                if slots:
                    self._printer.ams_slots = slots
                    logger.info("[%s] AMS updated: %d slots", self._name, len(slots))

        # --- callbacks on state transitions ---
        curr_state = self._printer.state
        if curr_state != prev_state:
            if curr_state == "RUNNING" and self._on_job_start:
                try:
                    self._on_job_start(self._printer)
                except Exception as exc:
                    logger.error("[%s] on_job_start callback error: %s", self._name, exc)
            elif curr_state == "FINISH" and self._on_job_finish:
                try:
                    self._on_job_finish(self._printer)
                except Exception as exc:
                    logger.error("[%s] on_job_finish callback error: %s", self._name, exc)

    # ---------------------------------------------------------------- commands

    def push_all(self) -> None:
        """Publish a pushall command to request a full state dump from the printer."""
        with self._client_lock:
            client = self._client
        if client is None:
            logger.warning("[%s] push_all called but client is not connected.", self._name)
            return
        payload = json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}})
        info = client.publish(self._request_topic, payload, qos=0)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.warning("[%s] push_all publish failed, rc=%d.", self._name, info.rc)

    # ---------------------------------------------------------------- run loop

    def start(self) -> None:
        """Connect and begin the network loop in a background thread."""
        if self._started:
            logger.warning("[%s] start() called more than once; ignoring.", self._name)
            return
        t = threading.Thread(
            target=self._run_loop,
            name=f"mqtt-{self._name}",
            daemon=True,
        )
        # Set _started only after the thread object is created so that a
        # ThreadError here does not leave the object permanently unusable.
        self._started = True
        t.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._client_lock:
            client = self._client
        if client is not None:
            try:
                client.disconnect()
            except Exception as e:
                logger.debug("[%s] Exception during disconnect: %s", self._name, e)

    def _run_loop(self) -> None:
        try:
            client = self._build_client()
            client.reconnect_delay_set(min_delay=1, max_delay=120)
            logger.info("[%s] Connecting to %s:%d …", self._name, self._host, MQTT_PORT)
            client.connect(self._host, MQTT_PORT, keepalive=60)
            with self._client_lock:
                self._client = client
            client.loop_forever(retry_first_connection=True)
        except Exception as exc:
            logger.error("[%s] MQTT loop error: %s", self._name, exc)
        finally:
            with self._client_lock:
                self._client = None
            logger.info("[%s] MQTT loop exited.", self._name)
