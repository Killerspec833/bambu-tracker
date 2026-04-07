from __future__ import annotations

import base64
from html import escape as h
from typing import Any

from flask import Blueprint, g, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from ..labels import label_png
from .common import (
    flash_msg,
    page,
    pagination_html,
    require_write,
    safe_color,
    status_badge,
)

inventory_bp = Blueprint("inventory", __name__)

_MATERIALS = ["PLA", "PETG", "ABS", "TPU", "ASA", "PA", "PC", "PVA", "HIPS", "Other"]
_PER_PAGE = 40


# ─── helpers ──────────────────────────────────────────────────────────────────

def _active_alerts() -> int:
    return len(g.inv.get_active_alerts())


def _pct_class(pct: int) -> str:
    if pct <= 15:
        return "pct-low"
    if pct <= 35:
        return "pct-mid"
    return "pct-ok"


# ─── dashboard ────────────────────────────────────────────────────────────────

@inventory_bp.route("/")
@login_required
def dashboard():
    inv = g.inv
    printers_list = inv.list_printers()
    low_spools = inv.spools_below_threshold()
    _, total_spools = inv.list_spools(per_page=1)
    active_alerts = inv.get_active_alerts()

    # Build enriched printer objects for the template
    enriched_printers = []
    for p in printers_list:
        ams = p.get("ams_data") or []
        slots_parts = []
        for slot in (ams if isinstance(ams, list) else []):
            color = safe_color(slot.get("color", "#aaa"))
            pct = max(0, min(100, int(slot.get("remaining_pct", 0))))
            mat = slot.get("material", "") or "—"
            pct_cls = _pct_class(pct)
            slots_parts.append(f"""<div class="slot-row">
              <span class="slot-idx">S{slot.get("index", "?")}</span>
              <span class="dot" style="background:{color}"></span>
              <span style="min-width:46px;font-size:.8rem">{h(mat)}</span>
              <div class="pct-bar"><div class="pct-fill {pct_cls}" style="width:{pct}%;background:{color}"></div></div>
              <span style="min-width:34px;font-size:.78rem;text-align:right">{pct}%</span>
            </div>""")
        seen = p.get("last_seen_at")
        enriched_printers.append({
            **p,
            "seen_str": str(seen)[:16].replace("T", " ") if seen else "never",
            "ams_slots_html": "".join(slots_parts),
        })

    # Enrich low_spools with safe color
    enriched_low = []
    for s in low_spools:
        enriched_low.append({
            "id": s.id,
            "name": s.name,
            "material": s.material,
            "color": safe_color(s.color),
            "remaining_g": s.remaining_g,
            "total_weight_g": s.total_weight_g,
            "printer_name": s.printer_name,
        })

    active_alerts_list = [dict(a) for a in active_alerts]

    return render_template(
        "dashboard.html",
        title="Dashboard",
        active_nav="Dashboard",
        alert_count=len(active_alerts),
        printers_list=enriched_printers,
        low_spools=enriched_low,
        total_spools=total_spools,
        active_alerts=active_alerts_list,
    )


# ─── inventory list ───────────────────────────────────────────────────────────

@inventory_bp.route("/inventory")
@login_required
def inventory_list():
    inv = g.inv
    q = request.args.get("q", "")
    material = request.args.get("material", "")
    brand = request.args.get("brand", "")
    low_stock = request.args.get("low_stock", "") == "1"
    sort = request.args.get("sort", "id")
    order = request.args.get("order", "asc")
    try:
        page_num = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page_num = 1

    allowed_sorts = {"id", "name", "material", "brand", "remaining_g", "updated_at"}
    if sort not in allowed_sorts:
        sort = "id"

    rows, total = inv.list_spools(
        q=q, material=material, brand=brand,
        low_stock_only=low_stock,
        sort=sort, order=order,
        page=page_num, per_page=_PER_PAGE,
    )

    msg = request.args.get("msg", "")
    kind = request.args.get("kind", "ok")

    def sort_link(col: str, label: str) -> str:
        new_order = "desc" if (sort == col and order == "asc") else "asc"
        arrow = " ↑" if (sort == col and order == "asc") else (" ↓" if sort == col else "")
        return f'<a href="/inventory?q={h(q)}&material={h(material)}&brand={h(brand)}&sort={col}&order={new_order}">{label}{arrow}</a>'

    # Enrich rows with computed fields
    enriched_rows = []
    for s in rows:
        color = safe_color(s.get("color_hex") or "#aaa")
        rem = float(s.get("remaining_g", 0))
        total_w = float(s.get("total_weight_g", 1000))
        location = h(s.get("printer_name") or "storage")
        if s.get("current_slot") is not None and s.get("printer_name"):
            location += f" / S{s['current_slot']}"
        enriched_rows.append({
            **s,
            "color_safe": color,
            "location_str": location,
        })

    base_url = f"/inventory?q={h(q)}&material={h(material)}&brand={h(brand)}&sort={sort}&order={order}"

    return render_template(
        "inventory_list.html",
        title="Inventory",
        active_nav="Inventory",
        alert_count=_active_alerts(),
        rows=enriched_rows,
        total=total,
        q=q,
        material=material,
        brand=brand,
        low_stock=low_stock,
        sort=sort,
        order=order,
        materials=_MATERIALS,
        sort_links={
            "name": sort_link("name", "Name"),
            "material": sort_link("material", "Material"),
            "brand": sort_link("brand", "Brand"),
            "remaining_g": sort_link("remaining_g", "Remaining"),
        },
        pagination=pagination_html(page_num, total, _PER_PAGE, base_url),
        msg=msg,
        kind=kind,
    )


# ─── spool detail ─────────────────────────────────────────────────────────────

@inventory_bp.route("/inventory/<int:spool_id>")
@login_required
def spool_detail(spool_id: int):
    inv = g.inv
    s = inv.get_spool_dict(spool_id)
    if not s:
        return redirect(url_for("inventory.inventory_list", msg="Spool not found.", kind="err"))
    history = inv.get_spool_history(spool_id, limit=30)
    location = inv.get_active_location(spool_id)

    color = safe_color(s.get("color_hex") or "#aaa")
    rem = float(s.get("remaining_g", 0))
    total_w = float(s.get("total_weight_g", 1000))
    pct = int(rem / total_w * 100) if total_w else 0
    pct_cls = _pct_class(pct)

    loc_str = "In storage"
    if location and location.get("printer_name"):
        loc_str = f'{h(location["printer_name"])} / AMS slot {location["ams_slot"]}'

    history_list = [dict(e) for e in history]

    return render_template(
        "spool_detail.html",
        title=s["name"],
        active_nav="Inventory",
        alert_count=_active_alerts(),
        s=s,
        spool_id=spool_id,
        color=color,
        rem=rem,
        total_w=total_w,
        pct=pct,
        pct_cls=pct_cls,
        loc_str=loc_str,
        history=history_list,
    )


# ─── add / edit / delete ──────────────────────────────────────────────────────

@inventory_bp.route("/inventory/add", methods=["GET", "POST"])
@login_required
@require_write
def inventory_add():
    if request.method == "GET":
        return render_template(
            "spool_form.html",
            title="Add Spool",
            active_nav="Inventory",
            alert_count=_active_alerts(),
            form_title="Add Spool",
            action="/inventory/add",
            values={},
            materials=_MATERIALS,
        )
    try:
        price_str = request.form.get("purchase_price", "").strip()
        price_cents = int(float(price_str) * 100) if price_str else None
        spool_id = g.inv.add_spool(
            name=request.form["name"].strip(),
            material=request.form.get("material", "PLA"),
            color=request.form.get("color", "#FFFFFF").strip(),
            brand=request.form.get("brand", "").strip(),
            total_weight_g=float(request.form.get("total_weight_g", 1000)),
            remaining_g=float(request.form.get("remaining_g", 1000)),
            low_stock_threshold_g=float(request.form.get("low_stock_threshold_g", 50)),
            purchase_date=request.form.get("purchase_date") or None,
            purchase_price_cents=price_cents,
            notes=request.form.get("notes", "").strip(),
            created_by=current_user.id,
        )
        g.inv.record_audit("spool.create", user_id=current_user.id, entity_type="spool", entity_id=spool_id)
        return redirect(url_for("inventory.inventory_list", msg="Spool added.", kind="ok"))
    except (ValueError, KeyError) as exc:
        return redirect(url_for("inventory.inventory_list", msg=f"Error: {exc}", kind="err"))


@inventory_bp.route("/inventory/<int:spool_id>/edit", methods=["GET", "POST"])
@login_required
@require_write
def inventory_edit(spool_id: int):
    inv = g.inv
    s = inv.get_spool_dict(spool_id)
    if not s:
        return redirect(url_for("inventory.inventory_list", msg="Spool not found.", kind="err"))
    if request.method == "GET":
        return render_template(
            "spool_form.html",
            title=f"Edit {s['name']}",
            active_nav="Inventory",
            alert_count=_active_alerts(),
            form_title="Edit Spool",
            action=f"/inventory/{spool_id}/edit",
            values=s,
            materials=_MATERIALS,
        )
    try:
        price_str = request.form.get("purchase_price", "").strip()
        price_cents = int(float(price_str) * 100) if price_str else None
        inv.update_spool(
            spool_id,
            user_id=current_user.id,
            name=request.form["name"].strip(),
            material=request.form.get("material", s["material"]),
            color_hex=request.form.get("color", s["color_hex"]).strip(),
            brand=request.form.get("brand", "").strip(),
            total_weight_g=float(request.form.get("total_weight_g", s["total_weight_g"])),
            remaining_g=float(request.form.get("remaining_g", s["remaining_g"])),
            low_stock_threshold_g=float(request.form.get("low_stock_threshold_g", s["low_stock_threshold_g"])),
            purchase_date=request.form.get("purchase_date") or None,
            purchase_price_cents=price_cents,
            notes=request.form.get("notes", "").strip(),
        )
        inv.record_audit("spool.update", user_id=current_user.id, entity_type="spool", entity_id=spool_id)
        return redirect(url_for("inventory.inventory_list", msg="Spool updated.", kind="ok"))
    except (ValueError, KeyError) as exc:
        return redirect(url_for("inventory.inventory_list", msg=f"Error: {exc}", kind="err"))


@inventory_bp.route("/inventory/<int:spool_id>/delete", methods=["POST"])
@login_required
@require_write
def inventory_delete(spool_id: int):
    g.inv.delete_spool(spool_id, user_id=current_user.id)
    g.inv.record_audit("spool.delete", user_id=current_user.id, entity_type="spool", entity_id=spool_id)
    return redirect(url_for("inventory.inventory_list", msg="Spool deleted.", kind="ok"))


@inventory_bp.route("/inventory/<int:spool_id>/adjust", methods=["POST"])
@login_required
@require_write
def inventory_adjust(spool_id: int):
    try:
        new_g = float(request.form.get("remaining_g", 0))
        note = request.form.get("note", "").strip()
        g.inv.manual_adjust(spool_id, new_g, note=note or "Manual adjustment", user_id=current_user.id)
    except ValueError as exc:
        return redirect(url_for("inventory.spool_detail", spool_id=spool_id, msg=str(exc), kind="err"))
    return redirect(url_for("inventory.spool_detail", spool_id=spool_id))


# ─── label generation ─────────────────────────────────────────────────────────

@inventory_bp.route("/labels/<int:spool_id>")
@login_required
def label_page(spool_id: int):
    inv = g.inv
    s = inv.get_spool_dict(spool_id)
    if not s:
        return redirect(url_for("inventory.inventory_list", msg="Spool not found.", kind="err"))

    bid = s.get("barcode_id") or "SPL?????"
    base_url = request.host_url.rstrip("/")

    try:
        c128_bytes = label_png(bid, "code128")
        c128_b64 = base64.b64encode(c128_bytes).decode()
    except Exception:
        c128_b64 = ""

    try:
        qr_bytes = label_png(bid, "qr", base_url=base_url)
        qr_b64 = base64.b64encode(qr_bytes).decode()
    except Exception:
        qr_b64 = ""

    color = safe_color(s.get("color_hex") or "#aaa")

    return render_template(
        "label_page.html",
        title=f"Label: {s['name']}",
        active_nav="Inventory",
        alert_count=_active_alerts(),
        s=s,
        spool_id=spool_id,
        bid=bid,
        base_url=base_url,
        c128_b64=c128_b64,
        qr_b64=qr_b64,
        color=color,
    )


@inventory_bp.route("/labels/<int:spool_id>/code128.png")
@login_required
def label_code128(spool_id: int):
    s = g.inv.get_spool_dict(spool_id)
    if not s:
        return "Not found", 404
    from flask import Response
    png = label_png(s.get("barcode_id", "SPL00000"), "code128")
    g.inv.record_label_generation(spool_id, "code128", user_id=current_user.id)
    return Response(png, mimetype="image/png",
                    headers={"Content-Disposition": f'attachment; filename="{s["barcode_id"]}_code128.png"'})


@inventory_bp.route("/labels/<int:spool_id>/qr.png")
@login_required
def label_qr(spool_id: int):
    s = g.inv.get_spool_dict(spool_id)
    if not s:
        return "Not found", 404
    from flask import Response
    base_url = request.host_url.rstrip("/")
    png = label_png(s.get("barcode_id", "SPL00000"), "qr", base_url=base_url)
    g.inv.record_label_generation(spool_id, "qr", user_id=current_user.id)
    return Response(png, mimetype="image/png",
                    headers={"Content-Disposition": f'attachment; filename="{s["barcode_id"]}_qr.png"'})


# ─── alerts list ─────────────────────────────────────────────────────────────

@inventory_bp.route("/alerts")
@login_required
def alerts_list():
    inv = g.inv
    show_acked = request.args.get("show_acked", "") == "1"
    alerts = inv.get_active_alerts() if not show_acked else inv.get_all_alerts(limit=200)
    return render_template(
        "alerts.html",
        title="Alerts",
        active_nav="",
        alert_count=len(inv.get_active_alerts()),
        alerts=[dict(a) for a in alerts],
        show_acked=show_acked,
    )


# ─── alert acknowledgement ────────────────────────────────────────────────────

@inventory_bp.route("/alerts/<int:alert_id>/ack", methods=["POST"])
@login_required
def ack_alert(alert_id: int):
    g.inv.acknowledge_alert(alert_id, current_user.id)
    return redirect(request.referrer or url_for("inventory.dashboard"))
