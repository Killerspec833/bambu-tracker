"""Initial schema: all tables for Bambu Tracker v2.

Revision ID: 001
Revises:
Create Date: 2026-04-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users ──────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="operator"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("username"),
        sa.UniqueConstraint("email"),
    )

    # ── printers ───────────────────────────────────────────────────────────────
    op.create_table(
        "printers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False, server_default=""),
        sa.Column("serial", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False, server_default="us"),
        sa.Column("cloud_username", sa.Text(), nullable=False, server_default=""),
        sa.Column("cloud_token_enc", sa.Text(), nullable=False, server_default=""),
        sa.Column("ams_unit_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("serial"),
    )

    # ── printer_state ──────────────────────────────────────────────────────────
    op.create_table(
        "printer_state",
        sa.Column("printer_id", sa.Integer(), sa.ForeignKey("printers.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("state", sa.Text(), nullable=False, server_default="IDLE"),
        sa.Column("current_job", sa.Text()),
        sa.Column("ams_data", sa.JSON()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
    )

    # ── spools ─────────────────────────────────────────────────────────────────
    op.create_table(
        "spools",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("barcode_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("material", sa.Text(), nullable=False, server_default="PLA"),
        sa.Column("color_hex", sa.Text(), nullable=False, server_default="#FFFFFF"),
        sa.Column("brand", sa.Text(), nullable=False, server_default=""),
        sa.Column("total_weight_g", sa.Numeric(8, 2), nullable=False, server_default="1000"),
        sa.Column("remaining_g", sa.Numeric(8, 2), nullable=False, server_default="1000"),
        sa.Column("low_stock_threshold_g", sa.Numeric(8, 2), nullable=False, server_default="50"),
        sa.Column("purchase_date", sa.Text()),
        sa.Column("purchase_price_cents", sa.Integer()),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("barcode_id"),
    )
    op.create_index("ix_spools_material", "spools", ["material"])
    op.create_index("ix_spools_brand", "spools", ["brand"])
    op.create_index("ix_spools_remaining_g", "spools", ["remaining_g"])

    # ── spool_locations ────────────────────────────────────────────────────────
    op.create_table(
        "spool_locations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("spool_id", sa.Integer(), sa.ForeignKey("spools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("printer_id", sa.Integer(), sa.ForeignKey("printers.id", ondelete="SET NULL")),
        sa.Column("ams_slot", sa.Integer()),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("unassigned_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_spool_locations_active", "spool_locations",
                    ["printer_id", "ams_slot"],
                    postgresql_where=sa.text("unassigned_at IS NULL"))

    # ── print_jobs ─────────────────────────────────────────────────────────────
    op.create_table(
        "print_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("printer_id", sa.Integer(), sa.ForeignKey("printers.id", ondelete="SET NULL")),
        sa.Column("printer_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("subtask_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False, server_default="RUNNING"),
        sa.Column("filament_used_g", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("estimated_g", sa.Numeric(8, 2)),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_print_jobs_printer_name", "print_jobs", ["printer_name"])
    op.create_index("ix_print_jobs_start_time", "print_jobs", ["start_time"])

    # ── stock_events ───────────────────────────────────────────────────────────
    op.create_table(
        "stock_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("spool_id", sa.Integer(), sa.ForeignKey("spools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("delta_g", sa.Numeric(8, 2), nullable=False),
        sa.Column("new_remaining_g", sa.Numeric(8, 2), nullable=False),
        sa.Column("printer_id", sa.Integer(), sa.ForeignKey("printers.id", ondelete="SET NULL")),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("print_jobs.id", ondelete="SET NULL")),
        sa.Column("performed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_stock_events_spool_id", "stock_events", ["spool_id"])
    op.create_index("ix_stock_events_timestamp", "stock_events", ["timestamp"])

    # ── scan_events ────────────────────────────────────────────────────────────
    op.create_table(
        "scan_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("barcode_id", sa.Text(), nullable=False),
        sa.Column("scanned_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("printer_id", sa.Integer(), sa.ForeignKey("printers.id", ondelete="SET NULL")),
        sa.Column("ams_slot", sa.Integer()),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_agent", sa.Text()),
        sa.Column("ip_address", sa.Text()),
    )

    # ── labels ─────────────────────────────────────────────────────────────────
    op.create_table(
        "labels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("spool_id", sa.Integer(), sa.ForeignKey("spools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbology", sa.Text(), nullable=False),
        sa.Column("generated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("printed_at", sa.DateTime(timezone=True)),
    )

    # ── alerts ─────────────────────────────────────────────────────────────────
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("alert_type", sa.Text(), nullable=False),
        sa.Column("spool_id", sa.Integer(), sa.ForeignKey("spools.id", ondelete="SET NULL")),
        sa.Column("printer_id", sa.Integer(), sa.ForeignKey("printers.id", ondelete="SET NULL")),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )

    # ── audit_log ──────────────────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text()),
        sa.Column("entity_id", sa.Integer()),
        sa.Column("old_value", sa.JSON()),
        sa.Column("new_value", sa.JSON()),
        sa.Column("ip_address", sa.Text()),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("audit_log")
    op.drop_table("alerts")
    op.drop_table("labels")
    op.drop_table("scan_events")
    op.drop_table("stock_events")
    op.drop_table("print_jobs")
    op.drop_table("spool_locations")
    op.drop_table("spools")
    op.drop_table("printer_state")
    op.drop_table("printers")
    op.drop_table("users")
