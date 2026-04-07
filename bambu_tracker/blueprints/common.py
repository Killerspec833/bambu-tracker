from __future__ import annotations

"""Shared CSS, HTML helpers, and decorators used by all blueprints."""

import re
from functools import wraps
from html import escape as h
from typing import Any, Callable

from flask import redirect, url_for
from flask_login import current_user
from flask_wtf.csrf import generate_csrf

# ─── colour guard ─────────────────────────────────────────────────────────────

_HEX_RE = re.compile(r'^#[0-9A-Fa-f]{3,8}$')


def safe_color(color: str) -> str:
    return color if _HEX_RE.match(color or "") else "#aaaaaa"


# ─── access control decorators ────────────────────────────────────────────────

def require_write(f: Callable) -> Callable:
    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not current_user.can_write():
            from flask import abort
            abort(403)
        return f(*args, **kwargs)
    return decorated


def require_admin(f: Callable) -> Callable:
    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not current_user.is_admin():
            from flask import abort
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ─── CSRF helper ──────────────────────────────────────────────────────────────

def csrf_token_input() -> str:
    """Return a hidden <input> carrying the current CSRF token.

    Include this in every HTML form that submits via POST so that
    Flask-WTF's CSRFProtect middleware accepts the request.
    """
    token = generate_csrf()
    return f'<input type="hidden" name="csrf_token" value="{token}">'


# ─── shared CSS ───────────────────────────────────────────────────────────────

BASE_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, -apple-system, sans-serif; background: #f3f4f6; color: #1f2937; min-height: 100vh; }
a { color: #4f46e5; text-decoration: none; }
a:hover { text-decoration: underline; }

header { background: #1e1b4b; color: #fff; padding: 12px 20px;
         display: flex; align-items: center; gap: 16px; position: sticky; top: 0; z-index: 100; }
header h1 { font-size: 1.1rem; white-space: nowrap; }
.nav-links { display: flex; gap: 4px; flex-wrap: wrap; }
.nav-links a { color: #c7d2fe; font-size: .85rem; padding: 4px 10px; border-radius: 4px; }
.nav-links a:hover { background: #3730a3; text-decoration: none; color: #fff; }
.nav-links a.active { background: #4f46e5; color: #fff; }
.nav-right { margin-left: auto; display: flex; align-items: center; gap: 12px; }
.nav-right span { font-size: .8rem; color: #a5b4fc; }
.alert-badge { background: #ef4444; color: #fff; border-radius: 50%;
               width: 18px; height: 18px; font-size: .7rem; font-weight: 700;
               display: flex; align-items: center; justify-content: center; }

main { padding: 20px; max-width: 1200px; margin: 0 auto; }
h2 { margin: 0 0 16px; font-size: 1.15rem; font-weight: 600; }
h3 { margin: 12px 0 8px; font-size: 1rem; font-weight: 600; }

.card { background: #fff; border-radius: 10px; padding: 20px;
        box-shadow: 0 1px 4px #0001; margin-bottom: 16px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }

table { width: 100%; border-collapse: collapse; background: #fff;
        border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px #0001; }
th { background: #f1f5f9; text-align: left; padding: 10px 14px; font-size: .82rem;
     font-weight: 600; color: #475569; white-space: nowrap; }
th a { color: #475569; display: flex; align-items: center; gap: 4px; }
td { padding: 9px 14px; font-size: .85rem; border-top: 1px solid #f1f5f9; vertical-align: middle; }
tr:hover td { background: #f8fafc; }

.badge { display: inline-flex; align-items: center; border-radius: 9999px;
         padding: 2px 10px; font-size: .73rem; font-weight: 600; white-space: nowrap; }
.badge-idle    { background: #e5e7eb; color: #6b7280; }
.badge-running { background: #dcfce7; color: #15803d; }
.badge-pause   { background: #fef9c3; color: #854d0e; }
.badge-finish  { background: #dbeafe; color: #1d4ed8; }
.badge-failed  { background: #fee2e2; color: #b91c1c; }
.badge-admin   { background: #ede9fe; color: #5b21b6; }
.badge-operator { background: #e0f2fe; color: #0369a1; }
.badge-viewer  { background: #f1f5f9; color: #475569; }

.dot { display: inline-block; width: 12px; height: 12px; border-radius: 50%;
       border: 1px solid #0002; vertical-align: middle; margin-right: 6px; flex-shrink: 0; }
.pct-bar { flex: 1; height: 7px; background: #e5e7eb; border-radius: 4px; overflow: hidden; min-width: 60px; }
.pct-fill { height: 100%; border-radius: 4px; }
.pct-low { background: #ef4444 !important; }
.pct-mid { background: #f59e0b !important; }
.pct-ok  { background: #22c55e !important; }

form.card label { display: block; margin-top: 14px; font-size: .85rem; font-weight: 600; color: #374151; }
input[type=text], input[type=email], input[type=password], input[type=number],
input[type=date], select, textarea {
    width: 100%; padding: 8px 12px; margin-top: 4px; border: 1px solid #d1d5db;
    border-radius: 6px; font-size: .9rem; background: #fff; color: #1f2937; }
input:focus, select:focus, textarea:focus { outline: 2px solid #4f46e5; border-color: transparent; }
input[type=checkbox] { width: auto; margin-right: 8px; }
.form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }

.btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 18px;
       border-radius: 6px; font-size: .88rem; font-weight: 600; cursor: pointer;
       border: none; text-decoration: none; transition: opacity .15s; }
.btn:hover { opacity: .85; text-decoration: none; }
.btn-primary { background: #4f46e5; color: #fff; }
.btn-secondary { background: #e5e7eb; color: #374151; }
.btn-danger { background: #ef4444; color: #fff; }
.btn-sm { padding: 4px 12px; font-size: .8rem; }
.btn-xs { padding: 2px 8px; font-size: .75rem; }
.btn-link { background: none; color: #4f46e5; padding: 0; font-weight: 400; }

.msg-ok  { background: #dcfce7; color: #15803d; padding: 10px 16px;
           border-radius: 6px; margin-bottom: 14px; font-size: .88rem; }
.msg-err { background: #fee2e2; color: #b91c1c; padding: 10px 16px;
           border-radius: 6px; margin-bottom: 14px; font-size: .88rem; }
.msg-warn { background: #fef9c3; color: #854d0e; padding: 10px 16px;
            border-radius: 6px; margin-bottom: 14px; font-size: .88rem; }

.filter-bar { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end;
              background: #fff; padding: 14px; border-radius: 8px;
              box-shadow: 0 1px 3px #0001; margin-bottom: 16px; }
.filter-bar label { font-size: .82rem; font-weight: 600; color: #374151; display: flex; flex-direction: column; gap: 4px; }
.filter-bar input, .filter-bar select { width: auto; min-width: 140px; }
.filter-bar .btn { align-self: flex-end; }

.pagination { display: flex; gap: 6px; align-items: center; margin-top: 14px; font-size: .85rem; }
.pagination a, .pagination span { padding: 5px 12px; border-radius: 5px; border: 1px solid #e5e7eb; }
.pagination a { color: #4f46e5; background: #fff; }
.pagination a:hover { background: #eef2ff; text-decoration: none; }
.pagination .current { background: #4f46e5; color: #fff; border-color: #4f46e5; }
.pagination .disabled { color: #9ca3af; background: #f9fafb; }

.stat-card { background: #fff; border-radius: 10px; padding: 20px;
             box-shadow: 0 1px 4px #0001; text-align: center; }
.stat-card .stat-val { font-size: 2rem; font-weight: 700; color: #4f46e5; }
.stat-card .stat-label { font-size: .82rem; color: #6b7280; margin-top: 4px; }

.slot-row { display: flex; align-items: center; gap: 10px; padding: 8px 0;
            border-bottom: 1px solid #f1f5f9; }
.slot-row:last-child { border-bottom: none; }
.slot-idx { min-width: 32px; font-size: .8rem; color: #6b7280; font-weight: 600; }

.warn-tag { color: #b45309; font-weight: 700; }
.empty-msg { color: #9ca3af; font-style: italic; padding: 20px; text-align: center; }

@media (max-width: 640px) {
  .grid { grid-template-columns: 1fr; }
  .form-row { grid-template-columns: 1fr; }
  .filter-bar { flex-direction: column; }
  td, th { padding: 7px 10px; }
  .nav-links a { font-size: .8rem; padding: 3px 7px; }
}
"""

# ─── layout factory ───────────────────────────────────────────────────────────

_LAYOUT = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bambu Tracker — {title}</title>
<style>{css}</style>
{extra_head}
</head>
<body>
{header}
<main>{body}</main>
</body>
</html>"""


def _nav_link(label: str, href: str, active: str) -> str:
    cls = ' class="active"' if active == label else ""
    return f'<a href="{href}"{cls}>{label}</a>'


def page(
    title: str,
    body: str,
    active_nav: str = "",
    extra_head: str = "",
    alert_count: int = 0,
) -> str:
    # Build nav
    nav = "".join([
        _nav_link("Dashboard", "/", active_nav),
        _nav_link("Inventory", "/inventory", active_nav),
        _nav_link("Printers", "/printers", active_nav),
        _nav_link("History", "/jobs", active_nav),
        _nav_link("Reports", "/reports", active_nav),
        _nav_link("Scanner", "/scan", active_nav),
    ])

    # Right side: alerts + user info
    from flask_login import current_user  # imported here to avoid circular
    user_html = ""
    admin_link = ""
    if current_user.is_authenticated:
        user_html = f'<span>{h(current_user.username)} <small>({h(current_user.role)})</small></span>'
        if current_user.is_admin():
            admin_link = '<a href="/admin" style="color:#c7d2fe;font-size:.8rem">Admin</a> '
        user_html += f' {admin_link}<a href="/logout" class="btn btn-sm btn-secondary" style="background:#3730a3;color:#c7d2fe;border:none">Logout</a>'

    alert_html = ""
    if alert_count > 0:
        alert_html = f'<a href="/alerts" title="{alert_count} active alert(s)" style="color:#fca5a5">' \
                     f'<span class="alert-badge">{alert_count}</span></a>'

    header = f"""<header>
  <h1>Bambu Tracker</h1>
  <nav class="nav-links">{nav}</nav>
  <div class="nav-right">{alert_html}{user_html}</div>
</header>"""

    return _LAYOUT.format(
        title=h(title),
        css=BASE_CSS,
        extra_head=extra_head,
        header=header,
        body=body,
    )


def flash_msg(msg: str, kind: str = "ok") -> str:
    if not msg:
        return ""
    cls = {"ok": "msg-ok", "err": "msg-err", "warn": "msg-warn"}.get(kind, "msg-ok")
    return f'<div class="{cls}">{h(msg)}</div>'


def pagination_html(page: int, total: int, per_page: int, base_url: str) -> str:
    pages = max(1, -(-total // per_page))
    if pages <= 1:
        return ""
    sep = "&" if "?" in base_url else "?"
    parts = [f'<div class="pagination">']
    if page > 1:
        parts.append(f'<a href="{base_url}{sep}page={page - 1}">&#8249; Prev</a>')
    else:
        parts.append('<span class="disabled">&#8249; Prev</span>')
    parts.append(f'<span class="current">{page}</span>')
    parts.append(f'<span style="color:#6b7280">of {pages}</span>')
    if page < pages:
        parts.append(f'<a href="{base_url}{sep}page={page + 1}">Next &#8250;</a>')
    else:
        parts.append('<span class="disabled">Next &#8250;</span>')
    parts.append("</div>")
    return "".join(parts)


def status_badge(status: str) -> str:
    cls = {
        "IDLE": "badge-idle",
        "RUNNING": "badge-running",
        "PAUSE": "badge-pause",
        "FINISH": "badge-finish",
        "FAILED": "badge-failed",
        "CANCELLED": "badge-idle",
    }.get(status.upper(), "badge-idle")
    return f'<span class="badge {cls}">{h(status)}</span>'
