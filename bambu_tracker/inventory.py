from __future__ import annotations

"""
Inventory: all database operations for spools, print jobs, locations,
alerts, scan events, and audit log.

The public API preserves the original printer_name-based signatures so
that existing MQTT callbacks in run.py continue to work unchanged. Internally
the class resolves names to printer_id FKs.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Integer,
    and_,
    delete,
    desc,
    func,
    insert,
    or_,
    select,
    text,
    update,
)

from .db import (
    db_alerts,
    audit_log,
    get_engine,
    labels,
    print_jobs,
    printer_state,
    printers,
    scan_events,
    spool_locations,
    spools,
    stock_events,
    users,
)
from .models import AmsSlot, FilamentSpool, PrintJob, Printer

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── barcode ID generation ────────────────────────────────────────────────────

def _next_barcode_id(conn: Any) -> str:
    """Generate the next SPLnnnnn barcode ID.

    Uses MAX() over the numeric suffix rather than MAX() over the text column
    so that lexicographic ordering of the string cannot produce gaps.  The
    caller must hold a transaction (BEGIN) so that no other writer can insert
    between our MAX read and the subsequent INSERT.
    """
    row = conn.execute(
        select(
            func.max(
                func.cast(
                    func.substr(spools.c.barcode_id, 4),
                    Integer,
                )
            )
        ).where(spools.c.barcode_id.like("SPL%"))
    ).fetchone()
    last_n: int | None = row[0] if row else None
    n = (last_n or 0) + 1
    return f"SPL{n:05d}"


# ─── printer name → id cache ──────────────────────────────────────────────────

class Inventory:
    """Central data access object. One instance shared across threads."""

    def __init__(self) -> None:
        # name → id cache; refreshed on demand; guarded by a lock because
        # MQTT threads and Flask request threads both call _printer_id().
        self._printer_name_cache: dict[str, int] = {}
        self._cache_lock = threading.Lock()

    def _printer_id(self, conn: Any, name: str) -> int | None:
        with self._cache_lock:
            cached = self._printer_name_cache.get(name)
        if cached is not None:
            return cached
        row = conn.execute(
            select(printers.c.id).where(printers.c.name == name)
        ).fetchone()
        if row:
            with self._cache_lock:
                self._printer_name_cache[name] = row[0]
            return row[0]
        return None

    def invalidate_printer_cache(self) -> None:
        with self._cache_lock:
            self._printer_name_cache.clear()

    # ─── printer management ───────────────────────────────────────────────────

    def upsert_printer(
        self,
        name: str,
        model: str,
        serial: str,
        region: str = "us",
        cloud_username: str = "",
        cloud_token_enc: str = "",
    ) -> int:
        """Insert or update printer row. Returns printer_id."""
        now = _now()
        with get_engine().begin() as conn:
            row = conn.execute(
                select(printers.c.id).where(printers.c.name == name)
            ).fetchone()
            if row:
                conn.execute(
                    update(printers)
                    .where(printers.c.id == row[0])
                    .values(
                        model=model,
                        serial=serial,
                        region=region,
                        cloud_username=cloud_username,
                        cloud_token_enc=cloud_token_enc,
                        updated_at=now,
                    )
                )
                printer_id = row[0]
            else:
                result = conn.execute(
                    insert(printers).values(
                        name=name,
                        model=model,
                        serial=serial,
                        region=region,
                        cloud_username=cloud_username,
                        cloud_token_enc=cloud_token_enc,
                        created_at=now,
                        updated_at=now,
                    ).returning(printers.c.id)
                )
                printer_id = result.fetchone()[0]
                # seed printer_state row
                conn.execute(
                    insert(printer_state).values(
                        printer_id=printer_id,
                        state="IDLE",
                        last_seen_at=now,
                    )
                )
        with self._cache_lock:
            self._printer_name_cache[name] = printer_id
        return printer_id

    def list_printers(self) -> list[dict[str, Any]]:
        with get_engine().connect() as conn:
            rows = conn.execute(
                select(
                    printers.c.id,
                    printers.c.name,
                    printers.c.model,
                    printers.c.serial,
                    printers.c.region,
                    printer_state.c.state,
                    printer_state.c.current_job,
                    printer_state.c.ams_data,
                    printer_state.c.last_seen_at,
                ).join(
                    printer_state,
                    printer_state.c.printer_id == printers.c.id,
                    isouter=True,
                ).order_by(printers.c.name)
            ).mappings().fetchall()
        return [dict(r) for r in rows]

    def get_printer(self, printer_id: int) -> dict[str, Any] | None:
        with get_engine().connect() as conn:
            row = conn.execute(
                select(
                    printers.c.id,
                    printers.c.name,
                    printers.c.model,
                    printers.c.serial,
                    printers.c.region,
                    printer_state.c.state,
                    printer_state.c.current_job,
                    printer_state.c.ams_data,
                    printer_state.c.last_seen_at,
                ).join(
                    printer_state,
                    printer_state.c.printer_id == printers.c.id,
                    isouter=True,
                ).where(printers.c.id == printer_id)
            ).mappings().fetchone()
        return dict(row) if row else None

    def upsert_printer_state(
        self,
        printer_name: str,
        state: str,
        current_job: str | None,
        ams_data: list[dict[str, Any]] | None,
    ) -> None:
        now = _now()
        with get_engine().begin() as conn:
            pid = self._printer_id(conn, printer_name)
            if pid is None:
                return
            conn.execute(
                text(
                    """
                    INSERT INTO printer_state (printer_id, state, current_job, ams_data, last_seen_at)
                    VALUES (:pid, :state, :job, :ams, :ts)
                    ON CONFLICT (printer_id) DO UPDATE
                        SET state       = EXCLUDED.state,
                            current_job = EXCLUDED.current_job,
                            ams_data    = EXCLUDED.ams_data,
                            last_seen_at = EXCLUDED.last_seen_at
                    """
                ),
                {
                    "pid": pid,
                    "state": state,
                    "job": current_job,
                    "ams": json.dumps(ams_data) if ams_data is not None else None,
                    "ts": now,
                },
            )

    # ─── spools ───────────────────────────────────────────────────────────────

    def add_spool(
        self,
        name: str,
        material: str,
        color: str,
        brand: str,
        total_weight_g: float,
        remaining_g: float,
        low_stock_threshold_g: float = 50.0,
        purchase_date: str | None = None,
        purchase_price_cents: int | None = None,
        notes: str = "",
        created_by: int | None = None,
        # legacy compat: ignored but accepted
        printer_name: str = "",
        ams_slot: int = 0,
    ) -> int:
        now = _now()
        with get_engine().begin() as conn:
            barcode_id = _next_barcode_id(conn)
            result = conn.execute(
                insert(spools).values(
                    barcode_id=barcode_id,
                    name=name,
                    material=material,
                    color_hex=color,
                    brand=brand,
                    total_weight_g=total_weight_g,
                    remaining_g=remaining_g,
                    low_stock_threshold_g=low_stock_threshold_g,
                    purchase_date=purchase_date,
                    purchase_price_cents=purchase_price_cents,
                    notes=notes,
                    is_archived=False,
                    created_by=created_by,
                    created_at=now,
                    updated_at=now,
                ).returning(spools.c.id)
            )
            spool_id = result.fetchone()[0]
            conn.execute(
                insert(stock_events).values(
                    spool_id=spool_id,
                    event_type="intake",
                    delta_g=float(total_weight_g),
                    new_remaining_g=float(remaining_g),
                    performed_by=created_by,
                    timestamp=now,
                    note="Initial intake",
                )
            )
            # legacy: if printer_name provided, create location
            if printer_name:
                pid = self._printer_id(conn, printer_name)
                if pid is not None:
                    conn.execute(
                        insert(spool_locations).values(
                            spool_id=spool_id,
                            printer_id=pid,
                            ams_slot=ams_slot,
                            assigned_at=now,
                            assigned_by=created_by,
                        )
                    )
        return spool_id

    def get_spool(self, spool_id: int) -> Optional[FilamentSpool]:
        with get_engine().connect() as conn:
            row = conn.execute(
                select(spools).where(spools.c.id == spool_id)
            ).mappings().fetchone()
        return _row_to_spool(dict(row)) if row else None

    def get_spool_dict(self, spool_id: int) -> dict[str, Any] | None:
        with get_engine().connect() as conn:
            row = conn.execute(
                select(spools).where(spools.c.id == spool_id)
            ).mappings().fetchone()
        return dict(row) if row else None

    def get_spool_by_barcode(self, barcode_id: str) -> dict[str, Any] | None:
        with get_engine().connect() as conn:
            row = conn.execute(
                select(spools).where(spools.c.barcode_id == barcode_id)
            ).mappings().fetchone()
        return dict(row) if row else None

    def get_spool_by_printer_slot(
        self, printer_name: str, ams_slot: int
    ) -> Optional[FilamentSpool]:
        """Return the spool currently loaded in the given printer+slot."""
        with get_engine().connect() as conn:
            pid = self._printer_id(conn, printer_name)
            if pid is None:
                return None
            row = conn.execute(
                select(spools)
                .join(spool_locations, spool_locations.c.spool_id == spools.c.id)
                .where(
                    and_(
                        spool_locations.c.printer_id == pid,
                        spool_locations.c.ams_slot == ams_slot,
                        spool_locations.c.unassigned_at.is_(None),
                    )
                )
            ).mappings().fetchone()
        return _row_to_spool(dict(row)) if row else None

    def list_spools(
        self,
        q: str = "",
        material: str = "",
        brand: str = "",
        low_stock_only: bool = False,
        include_archived: bool = False,
        sort: str = "id",
        order: str = "asc",
        page: int = 1,
        per_page: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """Returns (rows, total_count). Rows include current location info."""
        # current location sub-select
        active_loc = (
            select(
                spool_locations.c.spool_id,
                printers.c.name.label("printer_name"),
                spool_locations.c.ams_slot,
            )
            .join(printers, printers.c.id == spool_locations.c.printer_id, isouter=True)
            .where(spool_locations.c.unassigned_at.is_(None))
            .subquery("active_loc")
        )

        stmt = (
            select(
                spools,
                active_loc.c.printer_name,
                active_loc.c.ams_slot.label("current_slot"),
            )
            .join(active_loc, active_loc.c.spool_id == spools.c.id, isouter=True)
        )

        filters = []
        if not include_archived:
            filters.append(spools.c.is_archived == False)  # noqa: E712
        if q:
            like = f"%{q}%"
            filters.append(
                or_(
                    spools.c.name.ilike(like),
                    spools.c.brand.ilike(like),
                    spools.c.barcode_id.ilike(like),
                )
            )
        if material:
            filters.append(spools.c.material == material)
        if brand:
            filters.append(spools.c.brand == brand)
        if low_stock_only:
            filters.append(spools.c.remaining_g <= spools.c.low_stock_threshold_g)
        if filters:
            stmt = stmt.where(and_(*filters))

        sort_col = getattr(spools.c, sort, spools.c.id)
        stmt = stmt.order_by(sort_col.asc() if order == "asc" else sort_col.desc())

        with get_engine().connect() as conn:
            total = conn.execute(
                select(func.count()).select_from(stmt.subquery())
            ).scalar() or 0
            rows = conn.execute(
                stmt.limit(per_page).offset((page - 1) * per_page)
            ).mappings().fetchall()
        return [dict(r) for r in rows], int(total)

    def get_all_spools(self) -> list[FilamentSpool]:
        """Legacy method: return all non-archived spools as FilamentSpool."""
        with get_engine().connect() as conn:
            rows = conn.execute(
                select(spools)
                .where(spools.c.is_archived == False)  # noqa: E712
                .order_by(spools.c.id)
            ).mappings().fetchall()
        return [_row_to_spool(dict(r)) for r in rows]

    def get_spools_for_printer(self, printer_name: str) -> list[FilamentSpool]:
        with get_engine().connect() as conn:
            pid = self._printer_id(conn, printer_name)
            if pid is None:
                return []
            rows = conn.execute(
                select(spools)
                .join(spool_locations, spool_locations.c.spool_id == spools.c.id)
                .where(
                    and_(
                        spool_locations.c.printer_id == pid,
                        spool_locations.c.unassigned_at.is_(None),
                    )
                )
                .order_by(spool_locations.c.ams_slot)
            ).mappings().fetchall()
        return [_row_to_spool(dict(r)) for r in rows]

    def update_spool(self, spool_id: int, user_id: int | None = None, **fields: Any) -> bool:
        allowed = {
            "name", "material", "color_hex", "brand",
            "total_weight_g", "remaining_g", "low_stock_threshold_g",
            "purchase_date", "purchase_price_cents", "notes", "is_archived",
            # legacy compat aliases
            "color",
        }
        updates: dict[str, Any] = {}
        for k, v in fields.items():
            if k == "color":
                updates["color_hex"] = v
            elif k in allowed:
                updates[k] = v
        if not updates:
            return False
        updates["updated_at"] = _now()
        with get_engine().begin() as conn:
            result = conn.execute(
                update(spools).where(spools.c.id == spool_id).values(**updates)
            )
        return result.rowcount > 0

    def delete_spool(self, spool_id: int, user_id: int | None = None) -> bool:
        with get_engine().begin() as conn:
            # Close any open location
            conn.execute(
                update(spool_locations)
                .where(
                    and_(
                        spool_locations.c.spool_id == spool_id,
                        spool_locations.c.unassigned_at.is_(None),
                    )
                )
                .values(unassigned_at=_now())
            )
            result = conn.execute(delete(spools).where(spools.c.id == spool_id))
        return result.rowcount > 0

    def spools_below_threshold(self) -> list[FilamentSpool]:
        with get_engine().connect() as conn:
            rows = conn.execute(
                select(spools).where(
                    and_(
                        spools.c.remaining_g <= spools.c.low_stock_threshold_g,
                        spools.c.is_archived == False,  # noqa: E712
                    )
                )
            ).mappings().fetchall()
        return [_row_to_spool(dict(r)) for r in rows]

    def get_low_stock_spools(
        self, threshold_override: float | None = None
    ) -> list[FilamentSpool]:
        with get_engine().connect() as conn:
            if threshold_override is not None:
                cond = spools.c.remaining_g <= threshold_override
            else:
                cond = spools.c.remaining_g <= spools.c.low_stock_threshold_g
            rows = conn.execute(
                select(spools).where(
                    and_(cond, spools.c.is_archived == False)  # noqa: E712
                )
            ).mappings().fetchall()
        return [_row_to_spool(dict(r)) for r in rows]

    def deduct_usage(
        self,
        printer_name: str,
        slot_index: int,
        grams: float,
        note: str = "",
        job_id: int | None = None,
    ) -> Optional[FilamentSpool]:
        now = _now()
        with get_engine().begin() as conn:
            pid = self._printer_id(conn, printer_name)
            if pid is None:
                return None
            loc_row = conn.execute(
                select(spool_locations.c.spool_id)
                .where(
                    and_(
                        spool_locations.c.printer_id == pid,
                        spool_locations.c.ams_slot == slot_index,
                        spool_locations.c.unassigned_at.is_(None),
                    )
                )
            ).fetchone()
            if loc_row is None:
                return None
            spool_id = loc_row[0]
            spool_row = conn.execute(
                select(spools).where(spools.c.id == spool_id)
            ).mappings().fetchone()
            if not spool_row:
                return None
            old_remaining = float(spool_row["remaining_g"])
            new_remaining = max(0.0, old_remaining - grams)
            conn.execute(
                update(spools)
                .where(spools.c.id == spool_id)
                .values(remaining_g=new_remaining, updated_at=now)
            )
            conn.execute(
                insert(stock_events).values(
                    spool_id=spool_id,
                    event_type="deduct",
                    delta_g=-(old_remaining - new_remaining),
                    new_remaining_g=new_remaining,
                    printer_id=pid,
                    job_id=job_id,
                    timestamp=now,
                    note=note,
                )
            )
            updated = conn.execute(
                select(spools).where(spools.c.id == spool_id)
            ).mappings().fetchone()
        return _row_to_spool(dict(updated)) if updated else None

    def manual_adjust(
        self,
        spool_id: int,
        new_remaining_g: float,
        note: str = "",
        user_id: int | None = None,
    ) -> None:
        now = _now()
        with get_engine().begin() as conn:
            row = conn.execute(
                select(spools.c.remaining_g).where(spools.c.id == spool_id)
            ).fetchone()
            if not row:
                raise ValueError(f"Spool {spool_id} not found.")
            delta = new_remaining_g - float(row[0])
            conn.execute(
                update(spools)
                .where(spools.c.id == spool_id)
                .values(remaining_g=new_remaining_g, updated_at=now)
            )
            conn.execute(
                insert(stock_events).values(
                    spool_id=spool_id,
                    event_type="manual_set",
                    delta_g=delta,
                    new_remaining_g=new_remaining_g,
                    performed_by=user_id,
                    timestamp=now,
                    note=note,
                )
            )

    def get_spool_history(self, spool_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with get_engine().connect() as conn:
            rows = conn.execute(
                select(stock_events)
                .where(stock_events.c.spool_id == spool_id)
                .order_by(stock_events.c.timestamp.desc())
                .limit(limit)
            ).mappings().fetchall()
        return [dict(r) for r in rows]

    # ─── spool locations ──────────────────────────────────────────────────────

    def load_spool(
        self,
        spool_id: int,
        printer_id: int,
        ams_slot: int,
        user_id: int | None = None,
    ) -> str:
        """Load a spool into a printer slot. Returns 'ok', 'already_loaded', or 'conflict'."""
        now = _now()
        with get_engine().begin() as conn:
            # check if spool is already loaded somewhere
            existing = conn.execute(
                select(spool_locations.c.id)
                .where(
                    and_(
                        spool_locations.c.spool_id == spool_id,
                        spool_locations.c.unassigned_at.is_(None),
                    )
                )
            ).fetchone()
            if existing:
                return "already_loaded"
            # check if that slot is occupied
            conflict = conn.execute(
                select(spool_locations.c.id)
                .where(
                    and_(
                        spool_locations.c.printer_id == printer_id,
                        spool_locations.c.ams_slot == ams_slot,
                        spool_locations.c.unassigned_at.is_(None),
                    )
                )
            ).fetchone()
            if conflict:
                return "conflict"
            conn.execute(
                insert(spool_locations).values(
                    spool_id=spool_id,
                    printer_id=printer_id,
                    ams_slot=ams_slot,
                    assigned_at=now,
                    assigned_by=user_id,
                )
            )
            conn.execute(
                insert(stock_events).values(
                    spool_id=spool_id,
                    event_type="transfer",
                    delta_g=0,
                    new_remaining_g=float(
                        conn.execute(
                            select(spools.c.remaining_g).where(spools.c.id == spool_id)
                        ).scalar() or 0
                    ),
                    printer_id=printer_id,
                    performed_by=user_id,
                    timestamp=now,
                    note=f"Loaded into AMS slot {ams_slot}",
                )
            )
        return "ok"

    def unload_spool(self, spool_id: int, user_id: int | None = None) -> bool:
        now = _now()
        with get_engine().begin() as conn:
            result = conn.execute(
                update(spool_locations)
                .where(
                    and_(
                        spool_locations.c.spool_id == spool_id,
                        spool_locations.c.unassigned_at.is_(None),
                    )
                )
                .values(unassigned_at=now)
            )
            if result.rowcount > 0:
                conn.execute(
                    insert(stock_events).values(
                        spool_id=spool_id,
                        event_type="transfer",
                        delta_g=0,
                        new_remaining_g=float(
                            conn.execute(
                                select(spools.c.remaining_g).where(spools.c.id == spool_id)
                            ).scalar() or 0
                        ),
                        performed_by=user_id,
                        timestamp=now,
                        note="Unloaded from printer",
                    )
                )
        return result.rowcount > 0

    def get_active_location(self, spool_id: int) -> dict[str, Any] | None:
        with get_engine().connect() as conn:
            row = conn.execute(
                select(
                    spool_locations.c.printer_id,
                    spool_locations.c.ams_slot,
                    printers.c.name.label("printer_name"),
                )
                .join(printers, printers.c.id == spool_locations.c.printer_id, isouter=True)
                .where(
                    and_(
                        spool_locations.c.spool_id == spool_id,
                        spool_locations.c.unassigned_at.is_(None),
                    )
                )
            ).mappings().fetchone()
        return dict(row) if row else None

    # ─── print jobs ───────────────────────────────────────────────────────────

    def start_job(self, printer_name: str, subtask_name: str) -> int:
        now = _now()
        with get_engine().begin() as conn:
            pid = self._printer_id(conn, printer_name)
            result = conn.execute(
                insert(print_jobs).values(
                    printer_id=pid,
                    printer_name=printer_name,
                    subtask_name=subtask_name,
                    start_time=now,
                    status="RUNNING",
                    filament_used_g={},
                    created_at=now,
                ).returning(print_jobs.c.id)
            )
            return result.fetchone()[0]

    def finish_job(
        self,
        job_id: int,
        status: str,
        filament_used: dict[int, float],
        notes: str = "",
    ) -> None:
        with get_engine().begin() as conn:
            conn.execute(
                update(print_jobs)
                .where(print_jobs.c.id == job_id)
                .values(
                    end_time=_now(),
                    status=status,
                    filament_used_g=filament_used,
                    notes=notes,
                )
            )

    def log_print_job(
        self,
        printer_name: str,
        subtask_name: str,
        start_time: str | None,
        end_time: str | None,
        status: str,
        filament_used: dict,
    ) -> int:
        """Update active RUNNING job or insert completed job. Returns job id."""
        now = _now()
        with get_engine().begin() as conn:
            pid = self._printer_id(conn, printer_name)
            row = conn.execute(
                select(print_jobs.c.id)
                .where(
                    and_(
                        print_jobs.c.printer_name == printer_name,
                        print_jobs.c.status == "RUNNING",
                    )
                )
                .order_by(print_jobs.c.start_time.desc())
                .limit(1)
            ).fetchone()
            if row:
                conn.execute(
                    update(print_jobs)
                    .where(print_jobs.c.id == row[0])
                    .values(
                        end_time=end_time or now,
                        status=status,
                        filament_used_g=filament_used,
                    )
                )
                return row[0]
            result = conn.execute(
                insert(print_jobs).values(
                    printer_id=pid,
                    printer_name=printer_name,
                    subtask_name=subtask_name,
                    start_time=start_time or now,
                    end_time=end_time or now,
                    status=status,
                    filament_used_g=filament_used,
                    created_at=now,
                ).returning(print_jobs.c.id)
            )
            return result.fetchone()[0]

    def get_active_job_id(self, printer_name: str) -> int | None:
        with get_engine().connect() as conn:
            row = conn.execute(
                select(print_jobs.c.id)
                .where(
                    and_(
                        print_jobs.c.printer_name == printer_name,
                        print_jobs.c.status == "RUNNING",
                    )
                )
                .order_by(print_jobs.c.start_time.desc())
                .limit(1)
            ).fetchone()
        return row[0] if row else None

    def list_jobs(
        self,
        printer_name: str | None = None,
        status: str | None = None,
        limit: int = 100,
        page: int = 1,
        per_page: int | None = None,
    ) -> list[PrintJob]:
        if per_page is not None:
            limit = per_page
        stmt = select(print_jobs).order_by(print_jobs.c.start_time.desc())
        if printer_name:
            stmt = stmt.where(print_jobs.c.printer_name == printer_name)
        if status:
            stmt = stmt.where(print_jobs.c.status == status)
        stmt = stmt.limit(limit).offset((page - 1) * limit)
        with get_engine().connect() as conn:
            rows = conn.execute(stmt).mappings().fetchall()
        return [_row_to_job(dict(r)) for r in rows]

    def get_print_history(
        self, printer_name: str | None = None, limit: int = 50
    ) -> list[PrintJob]:
        return self.list_jobs(printer_name=printer_name, limit=limit)

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with get_engine().connect() as conn:
            row = conn.execute(
                select(print_jobs).where(print_jobs.c.id == job_id)
            ).mappings().fetchone()
        return dict(row) if row else None

    def count_jobs(
        self, printer_name: str | None = None, status: str | None = None
    ) -> int:
        stmt = select(func.count()).select_from(print_jobs)
        if printer_name:
            stmt = stmt.where(print_jobs.c.printer_name == printer_name)
        if status:
            stmt = stmt.where(print_jobs.c.status == status)
        with get_engine().connect() as conn:
            return conn.execute(stmt).scalar() or 0

    # ─── label generation ────────────────────────────────────────────────────

    def record_label_generation(
        self,
        spool_id: int,
        symbology: str,
        user_id: int | None = None,
    ) -> int:
        """Insert a labels row and return its id."""
        with get_engine().begin() as conn:
            result = conn.execute(
                insert(labels).values(
                    spool_id=spool_id,
                    symbology=symbology,
                    generated_by=user_id,
                    generated_at=_now(),
                ).returning(labels.c.id)
            )
            return result.fetchone()[0]

    # ─── scan events ──────────────────────────────────────────────────────────

    def record_scan_event(
        self,
        barcode_id: str,
        action: str,
        result: str,
        scanned_by: int | None = None,
        printer_id: int | None = None,
        ams_slot: int | None = None,
        user_agent: str = "",
        ip_address: str = "",
    ) -> None:
        with get_engine().begin() as conn:
            conn.execute(
                insert(scan_events).values(
                    barcode_id=barcode_id,
                    scanned_by=scanned_by,
                    action=action,
                    printer_id=printer_id,
                    ams_slot=ams_slot,
                    result=result,
                    timestamp=_now(),
                    user_agent=user_agent,
                    ip_address=ip_address,
                )
            )

    # ─── alerts ───────────────────────────────────────────────────────────────

    def create_alert(
        self,
        alert_type: str,
        message: str,
        spool_id: int | None = None,
        printer_id: int | None = None,
    ) -> int:
        with get_engine().begin() as conn:
            result = conn.execute(
                insert(db_alerts).values(
                    alert_type=alert_type,
                    spool_id=spool_id,
                    printer_id=printer_id,
                    message=message,
                    triggered_at=_now(),
                ).returning(db_alerts.c.id)
            )
            return result.fetchone()[0]

    def get_active_alerts(self) -> list[dict[str, Any]]:
        with get_engine().connect() as conn:
            rows = conn.execute(
                select(db_alerts)
                .where(db_alerts.c.acknowledged_at.is_(None))
                .order_by(db_alerts.c.triggered_at.desc())
            ).mappings().fetchall()
        return [dict(r) for r in rows]

    def get_all_alerts(self, limit: int = 200) -> list[dict[str, Any]]:
        with get_engine().connect() as conn:
            rows = conn.execute(
                select(db_alerts)
                .order_by(db_alerts.c.triggered_at.desc())
                .limit(limit)
            ).mappings().fetchall()
        return [dict(r) for r in rows]

    def acknowledge_alert(self, alert_id: int, user_id: int) -> bool:
        with get_engine().begin() as conn:
            acknowledged_by = conn.execute(
                select(users.c.id).where(users.c.id == user_id)
            ).scalar_one_or_none()
            result = conn.execute(
                update(db_alerts)
                .where(db_alerts.c.id == alert_id)
                .values(acknowledged_at=_now(), acknowledged_by=acknowledged_by)
            )
        return result.rowcount > 0

    # ─── audit log ────────────────────────────────────────────────────────────

    def record_audit(
        self,
        action: str,
        user_id: int | None = None,
        entity_type: str | None = None,
        entity_id: int | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
        ip_address: str = "",
    ) -> None:
        with get_engine().begin() as conn:
            conn.execute(
                insert(audit_log).values(
                    user_id=user_id,
                    action=action,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    old_value=old_value,
                    new_value=new_value,
                    ip_address=ip_address,
                    timestamp=_now(),
                )
            )

    def list_audit_log(
        self, limit: int = 200, user_id: int | None = None, action_like: str = ""
    ) -> list[dict[str, Any]]:
        stmt = (
            select(
                audit_log,
                users.c.username,
            )
            .join(users, users.c.id == audit_log.c.user_id, isouter=True)
            .order_by(audit_log.c.timestamp.desc())
            .limit(limit)
        )
        if user_id:
            stmt = stmt.where(audit_log.c.user_id == user_id)
        if action_like:
            stmt = stmt.where(audit_log.c.action.ilike(f"%{action_like}%"))
        with get_engine().connect() as conn:
            rows = conn.execute(stmt).mappings().fetchall()
        return [dict(r) for r in rows]

    # ─── chart data ───────────────────────────────────────────────────────────

    def chart_usage_by_material(self, days: int = 30) -> list[dict[str, Any]]:
        days = max(1, int(days))
        from datetime import timedelta
        cutoff = _now() - timedelta(days=days)
        with get_engine().connect() as conn:
            rows = conn.execute(
                select(
                    spools.c.material,
                    func.sum(-stock_events.c.delta_g).label("used_g"),
                )
                .join(spools, spools.c.id == stock_events.c.spool_id)
                .where(
                    and_(
                        stock_events.c.event_type == "deduct",
                        stock_events.c.timestamp >= cutoff,
                    )
                )
                .group_by(spools.c.material)
                .order_by(desc("used_g"))
            ).mappings().fetchall()
        return [dict(r) for r in rows]

    def chart_jobs_per_day(self, days: int = 30) -> list[dict[str, Any]]:
        days = max(1, int(days))
        from datetime import timedelta
        cutoff = _now() - timedelta(days=days)
        engine = get_engine()
        day_bucket = (
            func.date(print_jobs.c.start_time)
            if engine.dialect.name == "sqlite"
            else func.date_trunc("day", print_jobs.c.start_time)
        ).label("day")
        with engine.connect() as conn:
            rows = conn.execute(
                select(
                    day_bucket,
                    print_jobs.c.printer_name,
                    func.count().label("count"),
                )
                .where(
                    and_(
                        print_jobs.c.start_time >= cutoff,
                        print_jobs.c.status.in_(["FINISH", "FAILED", "RUNNING"]),
                    )
                )
                .group_by(day_bucket, print_jobs.c.printer_name)
                .order_by(day_bucket)
            ).mappings().fetchall()
        return [dict(r) for r in rows]

    def chart_stock_over_time(self, spool_id: int) -> list[dict[str, Any]]:
        with get_engine().connect() as conn:
            rows = conn.execute(
                select(
                    stock_events.c.timestamp,
                    stock_events.c.new_remaining_g,
                    stock_events.c.event_type,
                )
                .where(stock_events.c.spool_id == spool_id)
                .order_by(stock_events.c.timestamp)
            ).mappings().fetchall()
        return [dict(r) for r in rows]

    # ─── export helpers ───────────────────────────────────────────────────────

    def export_spools_dicts(self) -> list[dict[str, Any]]:
        """Return all spools as a list of dicts (for JSON backup; loads all into memory)."""
        rows, _ = self.list_spools(include_archived=True, per_page=100_000)
        return rows

    def export_spools_iter(self):
        """Yield spool dicts one at a time — for streaming CSV export."""
        with get_engine().connect() as conn:
            result = conn.execution_options(yield_per=200).execute(
                select(spools)
                .where(spools.c.is_archived == False)  # noqa: E712
                .order_by(spools.c.id)
            )
            for row in result.mappings():
                yield dict(row)

    def export_jobs_dicts(self) -> list[dict[str, Any]]:
        with get_engine().connect() as conn:
            rows = conn.execute(
                select(print_jobs).order_by(print_jobs.c.start_time.desc())
            ).mappings().fetchall()
        return [dict(r) for r in rows]

    def export_stock_events_dicts(self) -> list[dict[str, Any]]:
        with get_engine().connect() as conn:
            rows = conn.execute(
                select(stock_events).order_by(stock_events.c.timestamp.desc())
            ).mappings().fetchall()
        return [dict(r) for r in rows]


# ─── row converters ───────────────────────────────────────────────────────────

def _row_to_spool(row: dict[str, Any]) -> FilamentSpool:
    return FilamentSpool(
        id=row["id"],
        name=row["name"],
        material=row["material"],
        color=row.get("color_hex", row.get("color", "#FFFFFF")),
        brand=row.get("brand", ""),
        total_weight_g=float(row["total_weight_g"]),
        remaining_g=float(row["remaining_g"]),
        printer_name=row.get("printer_name") or "",
        ams_slot=row.get("current_slot") or row.get("ams_slot") or 0,
        low_stock_threshold_g=float(row.get("low_stock_threshold_g", 50)),
        barcode_id=row.get("barcode_id", ""),
    )


def _row_to_job(row: dict[str, Any]) -> PrintJob:
    fu = row.get("filament_used_g") or {}
    if isinstance(fu, str):
        try:
            fu = json.loads(fu)
        except Exception:
            fu = {}
    return PrintJob(
        id=row["id"],
        printer_name=row.get("printer_name", ""),
        subtask_name=row.get("subtask_name", ""),
        start_time=str(row.get("start_time", "")),
        end_time=str(row["end_time"]) if row.get("end_time") else None,
        status=row.get("status", ""),
        filament_used={int(k): float(v) for k, v in fu.items()},
    )
