from __future__ import annotations

import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


class AlertManager:
    def __init__(self, alerts_cfg: dict[str, Any]) -> None:
        self._desktop: bool = bool(alerts_cfg.get("desktop", True))
        self._openclaw: bool = bool(alerts_cfg.get("openclaw", True))

    def _send(self, message: str, urgency: str = "normal") -> None:
        if self._desktop:
            try:
                subprocess.run(
                    ["notify-send", "-u", urgency, "Bambu Tracker", message],
                    check=False,
                    timeout=5,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                logger.warning("notify-send failed: %s", exc)

        if self._openclaw:
            try:
                subprocess.run(
                    ["openclaw", "system", "event", "--text", message, "--mode", "now"],
                    check=False,
                    timeout=5,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                logger.warning("openclaw alert failed: %s", exc)

    def pre_print_insufficient(
        self,
        printer: str,
        material: str,
        job: str,
        needed_g: float,
        remaining_g: float,
        slot: int,
    ) -> None:
        msg = (
            f"\u26a0\ufe0f {printer}: Not enough {material} for {job}. "
            f"Need {needed_g:.0f}g, have {remaining_g:.0f}g on slot {slot}."
        )
        logger.warning(msg)
        self._send(msg, urgency="critical")

    def low_stock(
        self,
        spool_name: str,
        printer: str,
        slot: int,
        remaining_g: float,
    ) -> None:
        msg = (
            f"\U0001f7e1 {spool_name} on {printer} AMS slot {slot} "
            f"is low: {remaining_g:.0f}g remaining."
        )
        logger.warning(msg)
        self._send(msg, urgency="normal")

    def print_complete(
        self,
        printer: str,
        job: str,
        used_g: float,
        spool_name: str,
        remaining_g: float,
    ) -> None:
        msg = (
            f"\u2705 {printer} finished {job}. "
            f"Used {used_g:.0f}g. {spool_name} now has {remaining_g:.0f}g."
        )
        logger.info(msg)
        self._send(msg, urgency="low")

    def spool_empty(
        self,
        spool_name: str,
        printer: str,
        slot: int,
    ) -> None:
        msg = (
            f"\U0001f534 {spool_name} on {printer} slot {slot} "
            f"is empty or near empty. Reorder needed."
        )
        logger.warning(msg)
        self._send(msg, urgency="critical")
