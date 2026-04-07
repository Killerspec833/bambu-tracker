from __future__ import annotations

"""
SQLAlchemy Core schema for Bambu Tracker.

All tables are defined here as Table objects so that:
  - Alembic can autogenerate migrations from metadata
  - inventory.py can import and query them without an ORM
  - create_all() works for development setup

Call init_engine(url) once at startup; all other modules import `engine`.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    MetaData,
    Numeric,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.engine import URL, make_url
from sqlalchemy.engine import Engine

metadata = MetaData()

# ─── users ────────────────────────────────────────────────────────────────────

users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("username", Text, nullable=False, unique=True),
    Column("email", Text, nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    # admin | operator | viewer
    Column("role", Text, nullable=False, server_default="operator"),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_login_at", DateTime(timezone=True)),
)

# ─── printers ─────────────────────────────────────────────────────────────────

printers = Table(
    "printers",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", Text, nullable=False, unique=True),
    Column("model", Text, nullable=False, server_default=""),
    Column("serial", Text, nullable=False, unique=True),
    Column("region", Text, nullable=False, server_default="us"),
    Column("cloud_username", Text, nullable=False, server_default=""),
    Column("cloud_token_enc", Text, nullable=False, server_default=""),
    Column("ams_unit_count", Integer, nullable=False, server_default="1"),
    Column("low_stock_notify", Boolean, nullable=False, server_default="true"),
    Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# ─── printer_state ────────────────────────────────────────────────────────────

printer_state = Table(
    "printer_state",
    metadata,
    Column("printer_id", Integer, ForeignKey("printers.id", ondelete="CASCADE"), primary_key=True),
    Column("state", Text, nullable=False, server_default="IDLE"),
    Column("current_job", Text),
    Column("ams_data", JSON),       # list of {index, material, color, remaining_pct}
    Column("last_seen_at", DateTime(timezone=True)),
)

# ─── spools ───────────────────────────────────────────────────────────────────

spools = Table(
    "spools",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("barcode_id", Text, nullable=False, unique=True),   # e.g. SPL00042
    Column("name", Text, nullable=False),
    Column("material", Text, nullable=False, server_default="PLA"),
    Column("color_hex", Text, nullable=False, server_default="#FFFFFF"),
    Column("brand", Text, nullable=False, server_default=""),
    Column("total_weight_g", Numeric(8, 2), nullable=False, server_default="1000"),
    Column("remaining_g", Numeric(8, 2), nullable=False, server_default="1000"),
    Column("low_stock_threshold_g", Numeric(8, 2), nullable=False, server_default="50"),
    Column("purchase_date", Text),              # ISO date string
    Column("purchase_price_cents", Integer),    # store cents; display as currency
    Column("notes", Text, nullable=False, server_default=""),
    Column("is_archived", Boolean, nullable=False, server_default="false"),
    Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# ─── spool_locations ──────────────────────────────────────────────────────────

spool_locations = Table(
    "spool_locations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("spool_id", Integer, ForeignKey("spools.id", ondelete="CASCADE"), nullable=False),
    Column("printer_id", Integer, ForeignKey("printers.id", ondelete="SET NULL")),
    Column("ams_slot", Integer),                # 0-3 (or 0-15 for multi-AMS)
    Column("assigned_at", DateTime(timezone=True), nullable=False),
    Column("assigned_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("unassigned_at", DateTime(timezone=True)),   # NULL means still loaded
)

# ─── print_jobs ───────────────────────────────────────────────────────────────

print_jobs = Table(
    "print_jobs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("printer_id", Integer, ForeignKey("printers.id", ondelete="SET NULL")),
    # keep printer_name for display even if printer row is deleted
    Column("printer_name", Text, nullable=False, server_default=""),
    Column("subtask_name", Text, nullable=False, server_default=""),
    Column("start_time", DateTime(timezone=True), nullable=False),
    Column("end_time", DateTime(timezone=True)),
    Column("status", Text, nullable=False, server_default="RUNNING"),
    Column("filament_used_g", JSON, nullable=False, server_default="{}"),
    Column("estimated_g", Numeric(8, 2)),
    Column("notes", Text, nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# ─── stock_events ─────────────────────────────────────────────────────────────

stock_events = Table(
    "stock_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("spool_id", Integer, ForeignKey("spools.id", ondelete="CASCADE"), nullable=False),
    # intake | deduct | manual_set | transfer | archive
    Column("event_type", Text, nullable=False),
    Column("delta_g", Numeric(8, 2), nullable=False),
    Column("new_remaining_g", Numeric(8, 2), nullable=False),
    Column("printer_id", Integer, ForeignKey("printers.id", ondelete="SET NULL")),
    Column("job_id", Integer, ForeignKey("print_jobs.id", ondelete="SET NULL")),
    Column("performed_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("note", Text, nullable=False, server_default=""),
)

# ─── scan_events ──────────────────────────────────────────────────────────────

scan_events = Table(
    "scan_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("barcode_id", Text, nullable=False),
    Column("scanned_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("action", Text, nullable=False),     # lookup | load | unload
    Column("printer_id", Integer, ForeignKey("printers.id", ondelete="SET NULL")),
    Column("ams_slot", Integer),
    Column("result", Text, nullable=False),     # ok | not_found | already_loaded | conflict
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("user_agent", Text),
    Column("ip_address", Text),
)

# ─── labels ───────────────────────────────────────────────────────────────────

labels = Table(
    "labels",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("spool_id", Integer, ForeignKey("spools.id", ondelete="CASCADE"), nullable=False),
    Column("symbology", Text, nullable=False),  # code128 | qr
    Column("generated_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("generated_at", DateTime(timezone=True), nullable=False),
    Column("printed_at", DateTime(timezone=True)),
)

# ─── alerts ───────────────────────────────────────────────────────────────────

db_alerts = Table(
    "alerts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("alert_type", Text, nullable=False),     # low_stock | job_failed | printer_offline
    Column("spool_id", Integer, ForeignKey("spools.id", ondelete="SET NULL")),
    Column("printer_id", Integer, ForeignKey("printers.id", ondelete="SET NULL")),
    Column("message", Text, nullable=False),
    Column("triggered_at", DateTime(timezone=True), nullable=False),
    Column("acknowledged_at", DateTime(timezone=True)),
    Column("acknowledged_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
)

# ─── audit_log ────────────────────────────────────────────────────────────────

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("action", Text, nullable=False),     # e.g. spool.create | user.login
    Column("entity_type", Text),
    Column("entity_id", Integer),
    Column("old_value", JSON),
    Column("new_value", JSON),
    Column("ip_address", Text),
    Column("timestamp", DateTime(timezone=True), nullable=False),
)

# ─── engine singleton ─────────────────────────────────────────────────────────

_engine: Engine | None = None


def init_engine(url: str, echo: bool = False) -> Engine:
    """Create and store the global engine. Call once at startup."""
    global _engine
    parsed: URL = make_url(url)
    engine_kwargs: dict[str, object] = {"echo": echo}

    # Queue-pool sizing is valid for Postgres but breaks SQLite's default pool.
    if parsed.get_backend_name() != "sqlite":
        engine_kwargs.update(
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )

    _engine = create_engine(url, **engine_kwargs)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("DB engine not initialized. Call init_engine() first.")
    return _engine


def create_all_tables() -> None:
    """Create all tables if they don't exist. Useful for dev/test setup."""
    metadata.create_all(get_engine())
