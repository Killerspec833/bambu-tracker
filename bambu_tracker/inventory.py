from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from .models import FilamentSpool, PrintJob


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Inventory:
    def __init__(self, db_path: Path) -> None:
        self._path = str(db_path)
        self._init_db()

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS spools (
                    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                    name                 TEXT    NOT NULL,
                    material             TEXT    NOT NULL DEFAULT 'PLA',
                    color                TEXT    NOT NULL DEFAULT '#FFFFFF',
                    brand                TEXT    NOT NULL DEFAULT '',
                    total_weight_g       REAL    NOT NULL DEFAULT 1000.0,
                    remaining_g          REAL    NOT NULL DEFAULT 1000.0,
                    printer_name         TEXT    NOT NULL DEFAULT '',
                    ams_slot             INTEGER NOT NULL DEFAULT 0,
                    low_stock_threshold_g REAL   NOT NULL DEFAULT 50.0,
                    created_at           TEXT    NOT NULL,
                    updated_at           TEXT    NOT NULL
                );

                CREATE TABLE IF NOT EXISTS print_jobs (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    printer_name       TEXT    NOT NULL,
                    subtask_name       TEXT    NOT NULL DEFAULT '',
                    start_time         TEXT    NOT NULL,
                    end_time           TEXT,
                    status             TEXT    NOT NULL DEFAULT 'RUNNING',
                    filament_used_json TEXT    NOT NULL DEFAULT '{}',
                    notes              TEXT    NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS stock_events (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    spool_id      INTEGER NOT NULL REFERENCES spools(id),
                    event_type    TEXT    NOT NULL,
                    delta_g       REAL    NOT NULL,
                    new_remaining_g REAL  NOT NULL,
                    timestamp     TEXT    NOT NULL,
                    note          TEXT    NOT NULL DEFAULT ''
                );
            """)

    # ------------------------------------------------------------------ spools

    def add_spool(
        self,
        name: str,
        material: str,
        color: str,
        brand: str,
        total_weight_g: float,
        remaining_g: float,
        printer_name: str,
        ams_slot: int,
        low_stock_threshold_g: float = 50.0,
    ) -> int:
        now = _now_iso()
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO spools
                   (name, material, color, brand, total_weight_g, remaining_g,
                    printer_name, ams_slot, low_stock_threshold_g, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (name, material, color, brand, total_weight_g, remaining_g,
                 printer_name, ams_slot, low_stock_threshold_g, now, now),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def get_spool(self, spool_id: int) -> Optional[FilamentSpool]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM spools WHERE id = ?", (spool_id,)
            ).fetchone()
        return _row_to_spool(row) if row else None

    def get_spool_by_printer_slot(
        self, printer_name: str, ams_slot: int
    ) -> Optional[FilamentSpool]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM spools WHERE printer_name = ? AND ams_slot = ?",
                (printer_name, ams_slot),
            ).fetchone()
        return _row_to_spool(row) if row else None

    def list_spools(self) -> list[FilamentSpool]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM spools ORDER BY printer_name, ams_slot"
            ).fetchall()
        return [_row_to_spool(r) for r in rows]

    def spools_below_threshold(self) -> list[FilamentSpool]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM spools WHERE remaining_g <= low_stock_threshold_g"
            ).fetchall()
        return [_row_to_spool(r) for r in rows]

    def update_spool(self, spool_id: int, **fields: object) -> None:
        allowed = {
            "name", "material", "color", "brand", "total_weight_g",
            "remaining_g", "printer_name", "ams_slot", "low_stock_threshold_g",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = _now_iso()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [spool_id]
        with self._conn() as conn:
            conn.execute(
                f"UPDATE spools SET {set_clause} WHERE id = ?", values
            )

    def delete_spool(self, spool_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM spools WHERE id = ?", (spool_id,))

    def deduct_usage(
        self, spool_id: int, grams: float, note: str = ""
    ) -> float:
        """Deduct grams from spool, clamp to 0, record event. Returns new remaining_g."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT remaining_g FROM spools WHERE id = ?", (spool_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"Spool {spool_id} not found.")
            new_remaining = max(0.0, row["remaining_g"] - grams)
            now = _now_iso()
            conn.execute(
                "UPDATE spools SET remaining_g = ?, updated_at = ? WHERE id = ?",
                (new_remaining, now, spool_id),
            )
            conn.execute(
                """INSERT INTO stock_events
                   (spool_id, event_type, delta_g, new_remaining_g, timestamp, note)
                   VALUES (?,?,?,?,?,?)""",
                (spool_id, "deduct", -grams, new_remaining, now, note),
            )
        return new_remaining

    def manual_adjust(
        self, spool_id: int, new_remaining_g: float, note: str = ""
    ) -> None:
        """Manually set remaining_g and record the adjustment."""
        now = _now_iso()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT remaining_g FROM spools WHERE id = ?", (spool_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"Spool {spool_id} not found.")
            delta = new_remaining_g - row["remaining_g"]
            conn.execute(
                "UPDATE spools SET remaining_g = ?, updated_at = ? WHERE id = ?",
                (new_remaining_g, now, spool_id),
            )
            conn.execute(
                """INSERT INTO stock_events
                   (spool_id, event_type, delta_g, new_remaining_g, timestamp, note)
                   VALUES (?,?,?,?,?,?)""",
                (spool_id, "manual_set", delta, new_remaining_g, now, note),
            )

    # -------------------------------------------------------------- print jobs

    def start_job(self, printer_name: str, subtask_name: str) -> int:
        now = _now_iso()
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO print_jobs
                   (printer_name, subtask_name, start_time, status, filament_used_json)
                   VALUES (?,?,?,?,?)""",
                (printer_name, subtask_name, now, "RUNNING", "{}"),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def finish_job(
        self,
        job_id: int,
        status: str,
        filament_used: dict[int, float],
        notes: str = "",
    ) -> None:
        now = _now_iso()
        with self._conn() as conn:
            conn.execute(
                """UPDATE print_jobs
                   SET end_time=?, status=?, filament_used_json=?, notes=?
                   WHERE id=?""",
                (now, status, json.dumps(filament_used), notes, job_id),
            )

    def list_jobs(
        self, printer_name: Optional[str] = None, limit: int = 100
    ) -> list[PrintJob]:
        with self._conn() as conn:
            if printer_name:
                rows = conn.execute(
                    """SELECT * FROM print_jobs WHERE printer_name = ?
                       ORDER BY start_time DESC LIMIT ?""",
                    (printer_name, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM print_jobs ORDER BY start_time DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [_row_to_job(r) for r in rows]

    def get_active_job_id(self, printer_name: str) -> Optional[int]:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT id FROM print_jobs
                   WHERE printer_name = ? AND status = 'RUNNING'
                   ORDER BY start_time DESC LIMIT 1""",
                (printer_name,),
            ).fetchone()
        return row["id"] if row else None


# --------------------------------------------------------------- row helpers

def _row_to_spool(row: sqlite3.Row) -> FilamentSpool:
    return FilamentSpool(
        id=row["id"],
        name=row["name"],
        material=row["material"],
        color=row["color"],
        brand=row["brand"],
        total_weight_g=row["total_weight_g"],
        remaining_g=row["remaining_g"],
        printer_name=row["printer_name"],
        ams_slot=row["ams_slot"],
        low_stock_threshold_g=row["low_stock_threshold_g"],
    )


def _row_to_job(row: sqlite3.Row) -> PrintJob:
    try:
        filament_used = {
            int(k): v for k, v in json.loads(row["filament_used_json"]).items()
        }
    except (json.JSONDecodeError, ValueError):
        filament_used = {}
    return PrintJob(
        id=row["id"],
        printer_name=row["printer_name"],
        subtask_name=row["subtask_name"],
        start_time=row["start_time"],
        end_time=row["end_time"],
        status=row["status"],
        filament_used=filament_used,
    )
