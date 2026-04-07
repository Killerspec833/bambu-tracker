from __future__ import annotations

"""
JSON API blueprint: printer state polling (used by the live-refresh JS in printer detail page).
All endpoints require authentication.
"""

from flask import Blueprint, g, jsonify, request
from flask_login import login_required

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/printers")
@login_required
def api_printers():
    return jsonify(g.inv.list_printers())


@api_bp.route("/printers/<int:printer_id>/state")
@login_required
def api_printer_state(printer_id: int):
    p = g.inv.get_printer(printer_id)
    if not p:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "id": p["id"],
        "name": p["name"],
        "state": p.get("state") or "IDLE",
        "current_job": p.get("current_job"),
        "ams_data": p.get("ams_data") or [],
        "last_seen_at": str(p["last_seen_at"]) if p.get("last_seen_at") else None,
    })


@api_bp.route("/alerts")
@login_required
def api_alerts():
    return jsonify(g.inv.get_active_alerts())


@api_bp.route("/alerts/<int:alert_id>/ack", methods=["POST"])
@login_required
def api_ack_alert(alert_id: int):
    from flask_login import current_user
    ok = g.inv.acknowledge_alert(alert_id, current_user.id)
    return jsonify({"ok": ok})


@api_bp.route("/spools")
@login_required
def api_spools():
    """Return all non-archived spools as a JSON list (used by scanner and tests)."""
    rows, _ = g.inv.list_spools(per_page=1_000)
    return jsonify({"spools": rows})
