from __future__ import annotations

"""
Flask application factory.

Creates the Flask app, registers all blueprints, sets up Flask-Login,
and injects the Inventory instance into request context via g.
"""

import logging
from datetime import date, datetime
from typing import Any

from flask import Flask, g, redirect, url_for
from flask_wtf.csrf import CSRFProtect

from .auth import login_manager
from .limiter import limiter
from .blueprints.auth_bp import auth_bp
from .blueprints.inventory_bp import inventory_bp
from .blueprints.printers_bp import printers_bp
from .blueprints.scanner_bp import scanner_bp
from .blueprints.reports_bp import reports_bp
from .blueprints.api_bp import api_bp
from .inventory import Inventory

logger = logging.getLogger(__name__)

csrf = CSRFProtect()


def create_app(
    inv: Inventory,
    printers: dict[str, Any],   # kept for API compat with existing run.py signature
    config: dict[str, Any],
    secret_key: str = "",
) -> Flask:
    import secrets as _secrets
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = secret_key if secret_key else _secrets.token_hex(32)

    @app.template_filter("fmt_dt")
    def _fmt_dt(value: object, empty: str = "") -> str:
        if value is None:
            return empty
        if isinstance(value, datetime):
            return value.isoformat(sep=" ", timespec="minutes")
        if isinstance(value, date):
            return value.isoformat()
        return str(value).replace("T", " ")[:16]

    # ── Flask-Login ──────────────────────────────────────────────────────────
    login_manager.init_app(app)

    # ── CSRF protection ──────────────────────────────────────────────────────
    csrf.init_app(app)

    # ── Rate limiter ─────────────────────────────────────────────────────────
    limiter.init_app(app)

    # ── Inject inventory into request context ─────────────────────────────────
    @app.before_request
    def _inject_inv() -> None:
        g.inv = inv

    # ── Blueprints ────────────────────────────────────────────────────────────
    app.register_blueprint(auth_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(printers_bp)
    app.register_blueprint(scanner_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(api_bp)

    # Exempt pure-JSON API blueprints from CSRF (they use session auth, not forms)
    csrf.exempt(api_bp)
    csrf.exempt(scanner_bp)  # /api/scan is a JSON endpoint used by the mobile PWA

    # ── Rate limit exceeded handler ───────────────────────────────────────────
    @app.errorhandler(429)
    def _rate_limit_handler(e):
        from .blueprints.common import flash_msg, page
        body = flash_msg(
            "Too many login attempts. Please wait a minute and try again.", "err"
        )
        body += """
        <div style="max-width:380px;margin:60px auto">
          <div class="card" style="text-align:center">
            <p style="color:#6b7280;margin-top:8px">
              <a href="/login" class="btn btn-secondary">Back to Login</a>
            </p>
          </div>
        </div>"""
        return page("Too Many Requests", body), 429

    # ── Security headers ──────────────────────────────────────────────────────
    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    # ── Legacy redirect: /settings → /admin ──────────────────────────────────
    @app.route("/settings")
    def settings_redirect():
        return redirect(url_for("auth.admin_index"))

    # ── Legacy redirect: /history → /jobs ────────────────────────────────────
    @app.route("/history")
    def history_redirect():
        return redirect(url_for("printers.jobs_list"))

    return app
