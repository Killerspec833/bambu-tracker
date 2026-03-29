from __future__ import annotations

import logging
import re
from html import escape as h
from typing import Any

from flask import Flask, redirect, render_template_string, request, url_for

from .inventory import Inventory
from .models import Printer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTML templates
# ---------------------------------------------------------------------------

_BASE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, sans-serif; background: #f5f5f5; color: #222; }
header { background: #1a1a2e; color: #fff; padding: 12px 20px; display: flex;
         align-items: center; gap: 16px; }
header h1 { font-size: 1.2rem; }
nav a { color: #adf; text-decoration: none; margin-right: 16px; font-size: .9rem; }
nav a:hover { text-decoration: underline; }
main { padding: 20px; max-width: 1100px; margin: 0 auto; }
h2 { margin: 16px 0 10px; font-size: 1.1rem; }
table { width: 100%; border-collapse: collapse; background: #fff;
        border-radius: 6px; overflow: hidden; box-shadow: 0 1px 3px #0002; }
th { background: #e8e8f0; text-align: left; padding: 8px 12px; font-size: .85rem; }
td { padding: 8px 12px; font-size: .85rem; border-top: 1px solid #eee; }
tr:hover td { background: #f9f9ff; }
.badge { display: inline-block; border-radius: 4px; padding: 2px 8px;
         font-size: .75rem; font-weight: 600; }
.state-IDLE    { background: #ddd; color: #555; }
.state-RUNNING { background: #c8f0c8; color: #1a6b1a; }
.state-PAUSE   { background: #ffe8a0; color: #7a5500; }
.state-FINISH  { background: #c0e8ff; color: #005080; }
.state-FAILED  { background: #ffc8c8; color: #800000; }
.color-dot { display: inline-block; width: 14px; height: 14px; border-radius: 50%;
             border: 1px solid #0003; vertical-align: middle; margin-right: 6px; }
.card { background: #fff; border-radius: 8px; padding: 16px;
        box-shadow: 0 1px 3px #0002; margin-bottom: 16px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
        gap: 16px; }
.slot-row { display: flex; align-items: center; gap: 8px; padding: 6px 0;
            border-bottom: 1px solid #eee; }
.slot-row:last-child { border-bottom: none; }
.pct-bar { flex: 1; height: 8px; background: #e0e0e0; border-radius: 4px; overflow: hidden; }
.pct-fill { height: 100%; border-radius: 4px; }
form { background: #fff; border-radius: 8px; padding: 20px;
       box-shadow: 0 1px 3px #0002; max-width: 500px; }
label { display: block; margin-top: 12px; font-size: .85rem; font-weight: 600; }
input, select { width: 100%; padding: 6px 10px; margin-top: 4px;
                border: 1px solid #ccc; border-radius: 4px; font-size: .9rem; }
button, .btn { background: #1a1a2e; color: #fff; border: none; padding: 8px 18px;
               border-radius: 4px; cursor: pointer; font-size: .9rem; margin-top: 14px;
               text-decoration: none; display: inline-block; }
button:hover, .btn:hover { background: #2e2e5e; }
.btn-danger { background: #a00; }
.btn-danger:hover { background: #c00; }
.msg-ok  { background: #c8f0c8; color: #1a5a1a; padding: 8px 14px;
           border-radius: 4px; margin-bottom: 12px; }
.msg-err { background: #ffc8c8; color: #800; padding: 8px 14px;
           border-radius: 4px; margin-bottom: 12px; }
@media (max-width: 600px) { .grid { grid-template-columns: 1fr; }
  td, th { padding: 6px 8px; } }
"""

_LAYOUT = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bambu Tracker — {title}</title>
<style>{css}</style>
</head>
<body>
<header>
  <h1>🐼 Bambu Tracker</h1>
  <nav>
    <a href="/">Dashboard</a>
    <a href="/inventory">Inventory</a>
    <a href="/history">Print History</a>
    <a href="/settings">Settings</a>
  </nav>
</header>
<main>{body}</main>
</body>
</html>"""


_HEX_RE = re.compile(r'^#[0-9A-Fa-f]{3,8}$')


def _safe_color(color: str) -> str:
    """Return color if it's a valid CSS hex value, else a safe fallback."""
    return color if _HEX_RE.match(color) else "#aaaaaa"


def _page(title: str, body: str) -> str:
    return _LAYOUT.format(title=title, css=_BASE_CSS, body=body)


# ---------------------------------------------------------------------------

def create_app(
    inventory: Inventory,
    printers: dict[str, Printer],
    config: dict[str, Any],
) -> Flask:
    app = Flask(__name__)
    app.secret_key = "bambu-tracker-secret"

    # ---------------------------------------------------------------- Dashboard

    @app.route("/")
    def dashboard() -> str:
        cards_html = ""
        for pname, printer in printers.items():
            state_cls = f"state-{h(printer.state)}"
            slots_html = ""
            for slot in printer.ams_slots:
                color = _safe_color(slot.color)
                pct = max(0, min(100, slot.remaining_pct))
                slots_html += f"""
                <div class="slot-row">
                  <span style="min-width:28px;font-size:.8rem;color:#888">S{slot.index}</span>
                  <span class="color-dot" style="background:{color}"></span>
                  <span style="min-width:50px;font-size:.82rem">{h(slot.material) if slot.material else "—"}</span>
                  <div class="pct-bar">
                    <div class="pct-fill" style="width:{pct}%;background:{color}"></div>
                  </div>
                  <span style="min-width:36px;font-size:.8rem;text-align:right">{pct}%</span>
                </div>"""
            if not slots_html:
                slots_html = '<p style="color:#999;font-size:.85rem">No AMS data yet.</p>'
            job_html = (
                f'<p style="font-size:.85rem;margin-top:8px">Job: <b>{h(printer.current_job)}</b></p>'
                if printer.current_job
                else '<p style="font-size:.85rem;margin-top:8px;color:#999">No active job.</p>'
            )
            cards_html += f"""
            <div class="card">
              <div style="display:flex;justify-content:space-between;align-items:center">
                <strong>{h(pname)}</strong>
                <span class="badge {state_cls}">{h(printer.state)}</span>
              </div>
              <div style="font-size:.8rem;color:#888;margin-bottom:8px">{h(printer.model)} · {h(printer.serial)}</div>
              {slots_html}
              {job_html}
            </div>"""

        body = f'<h2>Printers</h2><div class="grid">{cards_html}</div>'
        return _page("Dashboard", body)

    # ---------------------------------------------------------------- Inventory

    @app.route("/inventory")
    def inventory_list() -> str:
        spools = inventory.list_spools()
        msg = request.args.get("msg", "")
        msg_type = request.args.get("msg_type", "ok")
        msg_html = ""
        if msg:
            css_cls = "msg-ok" if msg_type == "ok" else "msg-err"
            msg_html = f'<div class="{css_cls}">{h(msg)}</div>'

        rows = ""
        for s in spools:
            pct = max(0, min(100, int(s.remaining_g / s.total_weight_g * 100))) if s.total_weight_g else 0
            color = _safe_color(s.color)
            low = "⚠️ " if s.remaining_g <= s.low_stock_threshold_g else ""
            rows += f"""<tr>
              <td>{s.id}</td>
              <td><span class="color-dot" style="background:{color}"></span>{h(s.name)}</td>
              <td>{h(s.material)}</td>
              <td>{h(s.brand)}</td>
              <td>{h(s.printer_name)} / {s.ams_slot}</td>
              <td>{low}{s.remaining_g:.0f}g / {s.total_weight_g:.0f}g ({pct}%)</td>
              <td>
                <a href="/inventory/edit/{s.id}" class="btn" style="padding:3px 10px;font-size:.8rem">Edit</a>
                <form method="post" action="/inventory/delete/{s.id}" style="display:inline"
                      onsubmit="return confirm('Delete spool?')">
                  <button type="submit" class="btn btn-danger"
                          style="padding:3px 10px;font-size:.8rem">Del</button>
                </form>
              </td>
            </tr>"""

        table = f"""<table>
          <thead><tr>
            <th>ID</th><th>Name</th><th>Material</th><th>Brand</th>
            <th>Printer / Slot</th><th>Remaining</th><th>Actions</th>
          </tr></thead>
          <tbody>{rows or '<tr><td colspan="7" style="color:#999">No spools found.</td></tr>'}</tbody>
        </table>"""

        body = (
            f'{msg_html}<h2>Filament Inventory</h2>'
            f'<a href="/inventory/add" class="btn" style="margin-bottom:12px;display:inline-block">+ Add Spool</a>'
            f'{table}'
        )
        return _page("Inventory", body)

    @app.route("/inventory/add", methods=["GET"])
    def inventory_add_form() -> str:
        form = """
        <h2>Add Spool</h2>
        <form method="post" action="/inventory/add">
          <label>Name <input name="name" required></label>
          <label>Material
            <select name="material">
              <option>PLA</option><option>PETG</option><option>ABS</option>
              <option>TPU</option><option>ASA</option><option>PA</option>
              <option>PC</option><option>Other</option>
            </select>
          </label>
          <label>Color (hex) <input name="color" placeholder="#FF0000" value="#FFFFFF"></label>
          <label>Brand <input name="brand"></label>
          <label>Total weight (g) <input name="total_weight_g" type="number" step="1" value="1000"></label>
          <label>Remaining (g) <input name="remaining_g" type="number" step="1" value="1000"></label>
          <label>Printer name <input name="printer_name"></label>
          <label>AMS slot (0-3) <input name="ams_slot" type="number" min="0" max="3" value="0"></label>
          <label>Low stock threshold (g) <input name="low_stock_threshold_g" type="number" step="1" value="50"></label>
          <button type="submit">Add Spool</button>
          <a href="/inventory" class="btn" style="background:#888;margin-left:8px">Cancel</a>
        </form>"""
        return _page("Add Spool", form)

    @app.route("/inventory/add", methods=["POST"])
    def inventory_add() -> Any:
        try:
            inventory.add_spool(
                name=request.form["name"].strip(),
                material=request.form.get("material", "PLA"),
                color=request.form.get("color", "#FFFFFF").strip(),
                brand=request.form.get("brand", "").strip(),
                total_weight_g=float(request.form.get("total_weight_g", 1000)),
                remaining_g=float(request.form.get("remaining_g", 1000)),
                printer_name=request.form.get("printer_name", "").strip(),
                ams_slot=int(request.form.get("ams_slot", 0)),
                low_stock_threshold_g=float(request.form.get("low_stock_threshold_g", 50)),
            )
            return redirect(url_for("inventory_list", msg="Spool added.", msg_type="ok"))
        except (ValueError, KeyError) as exc:
            return redirect(url_for("inventory_list", msg=f"Error: {exc}", msg_type="err"))

    @app.route("/inventory/edit/<int:spool_id>", methods=["GET", "POST"])
    def inventory_edit(spool_id: int) -> Any:
        spool = inventory.get_spool(spool_id)
        if not spool:
            return redirect(url_for("inventory_list", msg="Spool not found.", msg_type="err"))

        if request.method == "POST":
            try:
                inventory.update_spool(
                    spool_id,
                    name=request.form["name"].strip(),
                    material=request.form.get("material", spool.material),
                    color=request.form.get("color", spool.color).strip(),
                    brand=request.form.get("brand", spool.brand).strip(),
                    total_weight_g=float(request.form.get("total_weight_g", spool.total_weight_g)),
                    remaining_g=float(request.form.get("remaining_g", spool.remaining_g)),
                    printer_name=request.form.get("printer_name", spool.printer_name).strip(),
                    ams_slot=int(request.form.get("ams_slot", spool.ams_slot)),
                    low_stock_threshold_g=float(
                        request.form.get("low_stock_threshold_g", spool.low_stock_threshold_g)
                    ),
                )
                return redirect(url_for("inventory_list", msg="Spool updated.", msg_type="ok"))
            except (ValueError, KeyError) as exc:
                return redirect(url_for("inventory_list", msg=f"Error: {exc}", msg_type="err"))

        form = f"""
        <h2>Edit Spool #{spool.id}</h2>
        <form method="post">
          <label>Name <input name="name" value="{h(spool.name)}" required></label>
          <label>Material <input name="material" value="{h(spool.material)}"></label>
          <label>Color (hex) <input name="color" value="{h(spool.color)}"></label>
          <label>Brand <input name="brand" value="{h(spool.brand)}"></label>
          <label>Total weight (g) <input name="total_weight_g" type="number" step="1" value="{spool.total_weight_g:.0f}"></label>
          <label>Remaining (g) <input name="remaining_g" type="number" step="1" value="{spool.remaining_g:.0f}"></label>
          <label>Printer name <input name="printer_name" value="{h(spool.printer_name)}"></label>
          <label>AMS slot (0-3) <input name="ams_slot" type="number" min="0" max="3" value="{spool.ams_slot}"></label>
          <label>Low stock threshold (g) <input name="low_stock_threshold_g" type="number" step="1" value="{spool.low_stock_threshold_g:.0f}"></label>
          <button type="submit">Save</button>
          <a href="/inventory" class="btn" style="background:#888;margin-left:8px">Cancel</a>
        </form>"""
        return _page("Edit Spool", form)

    @app.route("/inventory/delete/<int:spool_id>", methods=["POST"])
    def inventory_delete(spool_id: int) -> Any:
        inventory.delete_spool(spool_id)
        return redirect(url_for("inventory_list", msg="Spool deleted.", msg_type="ok"))

    # ---------------------------------------------------------------- Print History

    @app.route("/history")
    def history() -> str:
        filter_printer = request.args.get("printer", "")
        jobs = inventory.list_jobs(printer_name=filter_printer or None, limit=200)

        printer_options = "".join(
            f'<option value="{p}" {"selected" if p == filter_printer else ""}>{p}</option>'
            for p in sorted(printers.keys())
        )
        filter_form = f"""
        <form method="get" style="display:flex;gap:10px;align-items:flex-end;margin-bottom:14px">
          <label style="margin:0">Printer
            <select name="printer" style="width:auto">
              <option value="">All</option>{printer_options}
            </select>
          </label>
          <button type="submit" style="margin:0">Filter</button>
        </form>"""

        rows = ""
        for j in jobs:
            used_str = ", ".join(
                f"S{k}:{v:.0f}g" for k, v in sorted(j.filament_used.items())
            ) or "—"
            state_cls = f"state-{h(j.status)}"
            end = j.end_time[:19].replace("T", " ") if j.end_time else "—"
            start = j.start_time[:19].replace("T", " ") if j.start_time else "—"
            rows += f"""<tr>
              <td>{j.id}</td>
              <td>{h(j.printer_name)}</td>
              <td>{h(j.subtask_name) if j.subtask_name else "—"}</td>
              <td>{h(start)}</td>
              <td>{h(end)}</td>
              <td><span class="badge {state_cls}">{h(j.status)}</span></td>
              <td>{h(used_str)}</td>
            </tr>"""

        table = f"""<table>
          <thead><tr>
            <th>ID</th><th>Printer</th><th>Job</th>
            <th>Start</th><th>End</th><th>Status</th><th>Filament Used</th>
          </tr></thead>
          <tbody>{rows or '<tr><td colspan="7" style="color:#999">No jobs yet.</td></tr>'}</tbody>
        </table>"""

        body = f"<h2>Print History</h2>{filter_form}{table}"
        return _page("Print History", body)

    # ---------------------------------------------------------------- Settings

    @app.route("/settings", methods=["GET", "POST"])
    def settings() -> Any:
        alerts_cfg = config.get("alerts", {})
        msg = ""
        msg_type = "ok"

        if request.method == "POST":
            try:
                alerts = config.setdefault("alerts", {})
                alerts["low_stock_grams"] = float(
                    request.form.get("low_stock_grams", 50)
                )
                alerts["desktop"] = "desktop" in request.form
                alerts["openclaw"] = "openclaw" in request.form
                alerts["pre_print_check"] = "pre_print_check" in request.form
                msg = "Settings updated (runtime only — edit config.yaml to persist)."
            except ValueError as exc:
                msg = f"Error: {exc}"
                msg_type = "err"

        alerts_cfg = config.get("alerts", {})
        checked = lambda k: "checked" if alerts_cfg.get(k) else ""  # noqa: E731

        msg_html = ""
        if msg:
            msg_html = f'<div class="{"msg-ok" if msg_type == "ok" else "msg-err"}">{h(msg)}</div>'

        form = f"""
        {msg_html}
        <h2>Alert Settings</h2>
        <form method="post">
          <label>Low stock threshold (g)
            <input name="low_stock_grams" type="number" step="1"
                   value="{alerts_cfg.get('low_stock_grams', 50):.0f}">
          </label>
          <label style="display:flex;align-items:center;gap:8px;margin-top:14px">
            <input type="checkbox" name="desktop" {checked('desktop')} style="width:auto">
            Desktop notifications (notify-send)
          </label>
          <label style="display:flex;align-items:center;gap:8px;margin-top:8px">
            <input type="checkbox" name="openclaw" {checked('openclaw')} style="width:auto">
            OpenClaw notifications
          </label>
          <label style="display:flex;align-items:center;gap:8px;margin-top:8px">
            <input type="checkbox" name="pre_print_check" {checked('pre_print_check')} style="width:auto">
            Pre-print stock check
          </label>
          <button type="submit">Save</button>
        </form>
        <h2 style="margin-top:24px">Printers</h2>
        <table>
          <thead><tr><th>Name</th><th>Model</th><th>Serial</th><th>State</th></tr></thead>
          <tbody>{"".join(
            f'<tr><td>{h(p.name)}</td><td>{h(p.model)}</td>'
            f'<td><code>{h(p.serial)}</code></td>'
            f'<td><span class="badge state-{h(p.state)}">{h(p.state)}</span></td></tr>'
            for p in printers.values()
          )}</tbody>
        </table>"""

        return _page("Settings", form)

    return app
