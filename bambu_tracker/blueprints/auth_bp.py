from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import login_required, login_user, logout_user, current_user

from ..auth import (
    check_password,
    create_user,
    get_user_by_username,
    list_users,
    record_login,
    set_user_active,
)
from ..limiter import limiter
from .common import require_admin

auth_bp = Blueprint("auth", __name__)


# ─── login / logout ───────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("inventory.dashboard"))

    msg, kind = "", "ok"
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_user_by_username(username)
        if user and user.is_active and check_password(password, user._row["password_hash"]):
            login_user(user, remember=True)
            record_login(user.id)
            next_url = request.args.get("next") or url_for("inventory.dashboard")
            # Prevent open redirect: only allow relative paths
            from urllib.parse import urlparse
            parsed = urlparse(next_url)
            if parsed.netloc:
                next_url = url_for("inventory.dashboard")
            return redirect(next_url)
        msg, kind = "Invalid username or password.", "err"

    return render_template("login.html", title="Login", msg=msg, kind=kind,
                           active_nav="", alert_count=0)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


# ─── admin: user management ───────────────────────────────────────────────────

@auth_bp.route("/admin")
@login_required
@require_admin
def admin_index():
    all_users = list_users()
    return render_template("admin_index.html", title="Admin", users=all_users,
                           active_nav="", alert_count=0)


@auth_bp.route("/admin/user/new", methods=["GET", "POST"])
@login_required
@require_admin
def admin_new_user():
    msg, kind = "", "ok"
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "operator")
        # Whitelist roles to prevent privilege escalation via form manipulation
        if role not in ("admin", "operator", "viewer"):
            role = "operator"
        if not username or not email or not password:
            msg, kind = "All fields are required.", "err"
        elif len(password) < 8:
            msg, kind = "Password must be at least 8 characters.", "err"
        else:
            try:
                create_user(username, email, password, role)
                return redirect(url_for("auth.admin_index"))
            except Exception as exc:
                msg, kind = f"Error: {exc}", "err"

    return render_template("admin_new_user.html", title="New User", msg=msg, kind=kind,
                           active_nav="", alert_count=0)


@auth_bp.route("/admin/user/<int:user_id>/enable", methods=["POST"])
@login_required
@require_admin
def admin_enable_user(user_id: int):
    set_user_active(user_id, True)
    return redirect(url_for("auth.admin_index"))


@auth_bp.route("/admin/user/<int:user_id>/disable", methods=["POST"])
@login_required
@require_admin
def admin_disable_user(user_id: int):
    set_user_active(user_id, False)
    return redirect(url_for("auth.admin_index"))


@auth_bp.route("/admin/audit")
@login_required
@require_admin
def admin_audit():
    from flask import g
    inv = g.inv
    q_action = request.args.get("action", "")
    entries = inv.list_audit_log(limit=300, action_like=q_action)
    # Convert mapping objects to plain dicts for template use
    entries = [dict(e) for e in entries]
    return render_template("audit_log.html", title="Audit Log", entries=entries,
                           q_action=q_action, active_nav="", alert_count=0)
