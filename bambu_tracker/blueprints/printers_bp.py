from __future__ import annotations

from html import escape as h

from flask import Blueprint, g, redirect, render_template, request, url_for
from flask_login import login_required

from .common import pagination_html, safe_color, status_badge

printers_bp = Blueprint("printers", __name__)

_PER_PAGE = 50


def _active_alerts() -> int:
    return len(g.inv.get_active_alerts())


def _dur_str(start_time, end_time) -> str:
    if not (start_time and end_time):
        return "—"
    from datetime import datetime
    try:
        s_dt = datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
        e_dt = datetime.fromisoformat(str(end_time).replace("Z", "+00:00"))
        secs = int((e_dt - s_dt).total_seconds())
        return f"{secs//3600}h {(secs%3600)//60}m"
    except Exception:
        return "—"


def _dur_str_full(start_time, end_time) -> str:
    if not (start_time and end_time):
        return "—"
    from datetime import datetime
    try:
        s_dt = datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
        e_dt = datetime.fromisoformat(str(end_time).replace("Z", "+00:00"))
        secs = int((e_dt - s_dt).total_seconds())
        return f"{secs//3600}h {(secs%3600)//60}m {secs%60}s"
    except Exception:
        return "—"


# ─── printer list ─────────────────────────────────────────────────────────────

@printers_bp.route("/printers")
@login_required
def printers_list():
    inv = g.inv
    all_printers = inv.list_printers()

    enriched = []
    for p in all_printers:
        seen = p.get("last_seen_at")
        enriched.append({
            **p,
            "seen_str": str(seen)[:16].replace("T", " ") if seen else "never",
        })

    return render_template(
        "printers_list.html",
        title="Printers",
        active_nav="Printers",
        alert_count=_active_alerts(),
        printers=enriched,
    )


# ─── printer detail ───────────────────────────────────────────────────────────

@printers_bp.route("/printers/<int:printer_id>")
@login_required
def printer_detail(printer_id: int):
    inv = g.inv
    p = inv.get_printer(printer_id)
    if not p:
        return redirect(url_for("printers.printers_list"))

    ams = p.get("ams_data") or []

    # Build AMS slot rows
    ams_slots = []
    for slot in (ams if isinstance(ams, list) else []):
        idx = slot.get("index", "?")
        color = safe_color(slot.get("color", "#aaa"))
        mat = slot.get("material") or "—"
        pct = max(0, min(100, int(slot.get("remaining_pct", 0))))
        spool_link = "—"
        spool_obj = inv.get_spool_by_printer_slot(p["name"], idx) if isinstance(idx, int) else None
        if spool_obj:
            spool_link = f'<a href="/inventory/{spool_obj.id}">{h(spool_obj.name)}</a> <small>({spool_obj.barcode_id})</small>'
        ams_slots.append({
            "index": idx,
            "color": color,
            "material": mat,
            "pct": pct,
            "spool_link": spool_link,
        })

    # Recent jobs
    try:
        page_num = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page_num = 1
    jobs_raw = inv.list_jobs(printer_name=p["name"], limit=_PER_PAGE, page=page_num)
    total_jobs = inv.count_jobs(printer_name=p["name"])

    jobs = []
    for j in jobs_raw:
        used_str = ", ".join(f"S{k}:{v:.0f}g" for k, v in sorted(j.filament_used.items())) or "—"
        jobs.append({
            "id": j.id,
            "subtask_name": j.subtask_name,
            "start_str": str(j.start_time)[:16].replace("T", " "),
            "end_str": str(j.end_time)[:16].replace("T", " ") if j.end_time else "—",
            "dur_str": _dur_str(j.start_time, j.end_time),
            "status": j.status,
            "used_str": used_str,
        })

    seen = p.get("last_seen_at")

    return render_template(
        "printer_detail.html",
        title=p["name"],
        active_nav="Printers",
        alert_count=_active_alerts(),
        p={
            **p,
            "seen_str": str(seen)[:16].replace("T", " ") if seen else "never",
        },
        printer_id=printer_id,
        ams_slots=ams_slots,
        jobs=jobs,
        pagination=pagination_html(page_num, total_jobs, _PER_PAGE, f"/printers/{printer_id}?"),
    )


# ─── job history (global) ─────────────────────────────────────────────────────

@printers_bp.route("/jobs")
@login_required
def jobs_list():
    inv = g.inv
    q_printer = request.args.get("printer", "")
    q_status = request.args.get("status", "")
    try:
        page_num = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page_num = 1

    jobs_raw = inv.list_jobs(
        printer_name=q_printer or None,
        status=q_status or None,
        limit=_PER_PAGE,
        page=page_num,
    )
    total = inv.count_jobs(printer_name=q_printer or None, status=q_status or None)
    all_printers = inv.list_printers()

    jobs = []
    for j in jobs_raw:
        used_str = ", ".join(f"S{k}:{v:.0f}g" for k, v in sorted(j.filament_used.items())) or "—"
        jobs.append({
            "id": j.id,
            "printer_name": j.printer_name,
            "subtask_name": j.subtask_name,
            "start_str": str(j.start_time)[:16].replace("T", " "),
            "end_str": str(j.end_time)[:16].replace("T", " ") if j.end_time else "—",
            "status": j.status,
            "used_str": used_str,
        })

    base_url = f"/jobs?printer={h(q_printer)}&status={h(q_status)}"

    return render_template(
        "jobs_list.html",
        title="Print History",
        active_nav="History",
        alert_count=_active_alerts(),
        jobs=jobs,
        total=total,
        q_printer=q_printer,
        q_status=q_status,
        all_printers=all_printers,
        statuses=["RUNNING", "FINISH", "FAILED", "PAUSED", "CANCELLED"],
        pagination=pagination_html(page_num, total, _PER_PAGE, base_url),
    )


# ─── job detail ───────────────────────────────────────────────────────────────

@printers_bp.route("/jobs/<int:job_id>")
@login_required
def job_detail(job_id: int):
    inv = g.inv
    j = inv.get_job(job_id)
    if not j:
        return redirect(url_for("printers.jobs_list"))

    fu = j.get("filament_used_g") or {}
    if isinstance(fu, str):
        import json
        try:
            fu = json.loads(fu)
        except Exception:
            fu = {}

    start = str(j.get("start_time", ""))[:16].replace("T", " ")
    end = str(j.get("end_time", ""))[:16].replace("T", " ") if j.get("end_time") else "—"
    dur_str = _dur_str_full(j.get("start_time"), j.get("end_time"))
    total_used = sum(float(v) for v in fu.values())

    filament_rows = []
    for slot_idx, grams in sorted(fu.items(), key=lambda x: int(x[0])):
        spool = inv.get_spool_by_printer_slot(j.get("printer_name", ""), int(slot_idx))
        spool_link = f'<a href="/inventory/{spool.id}">{h(spool.name)}</a>' if spool else "unknown"
        filament_rows.append({"slot_idx": slot_idx, "spool_link": spool_link, "grams": grams})

    return render_template(
        "job_detail.html",
        title=f"Job #{job_id}",
        active_nav="History",
        alert_count=_active_alerts(),
        job_id=job_id,
        j=j,
        start=start,
        end=end,
        dur_str=dur_str,
        total_used=total_used,
        filament_rows=filament_rows,
    )
