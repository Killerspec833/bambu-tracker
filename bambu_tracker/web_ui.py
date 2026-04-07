from __future__ import annotations

"""
Flask application factory.

Creates the Flask app, registers all blueprints, sets up Flask-Login,
and injects the Inventory instance into request context via g.
"""

import logging
import os
from datetime import date, datetime
from typing import Any

from flask import Flask, g, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
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

    # ── Settings page ─────────────────────────────────────────────────────────
    alerts_cfg = config.get("alerts", {})
    _settings_state: dict[str, Any] = {
        "low_stock_grams": int(alerts_cfg.get("low_stock_grams", 50)),
        "pre_print_check": bool(alerts_cfg.get("pre_print_check", True)),
        "desktop": bool(alerts_cfg.get("desktop", True)),
        "openclaw": bool(alerts_cfg.get("openclaw", True)),
    }

    @app.route("/settings", methods=["GET", "POST"])
    @login_required
    def settings_page():
        msg, kind = "", "ok"
        if request.method == "POST":
            try:
                val = max(0, int(request.form.get("low_stock_grams", 50)))
            except (ValueError, TypeError):
                val = 50
            _settings_state["low_stock_grams"] = val
            _settings_state["pre_print_check"] = "pre_print_check" in request.form
            _settings_state["desktop"] = "desktop_notify" in request.form
            _settings_state["openclaw"] = "openclaw_notify" in request.form
            # Propagate into the live AlertManager config dict so changes take
            # effect immediately without a restart.
            alerts_cfg["low_stock_grams"] = val
            alerts_cfg["pre_print_check"] = _settings_state["pre_print_check"]
            alerts_cfg["desktop"] = _settings_state["desktop"]
            alerts_cfg["openclaw"] = _settings_state["openclaw"]
            msg, kind = "Settings saved.", "ok"
        return render_template(
            "settings.html",
            title="Settings",
            active_nav="Settings",
            alert_count=len(g.inv.get_active_alerts()),
            cfg=_settings_state,
            msg=msg,
            kind=kind,
        )

    # ── Bambu Cloud token refresh ─────────────────────────────────────────────
    _token_file: str = os.path.expanduser(
        config.get("cloud_auth", {}).get("token_file", "~/.bambu_token")
    )
    app.config["BAMBU_TOKEN_FILE"] = _token_file

    @app.route("/printers/refresh-token", methods=["POST"])
    @login_required
    @limiter.limit("5 per minute")
    def refresh_bambu_token():
        import json as _json
        import urllib.request as _req
        import urllib.error as _err

        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        if not email or not password:
            return jsonify({"ok": False, "error": "Email and password are required."}), 400

        payload = _json.dumps({"account": email, "password": password}).encode()
        api_req = _req.Request(
            "https://bambulab.com/api/sign-in/form",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "BambuTracker/1.0"},
            method="POST",
        )
        try:
            with _req.urlopen(api_req, timeout=15) as resp:
                body = resp.read().decode()
        except _err.HTTPError as e:
            body = e.read().decode()
            try:
                msg = _json.loads(body).get("message") or str(e)
            except Exception:
                msg = str(e)
            if e.code == 400:
                msg = "Login failed — if MFA is enabled, approve the Bambu app prompt first then retry."
            elif e.code == 401:
                msg = "Invalid email or password."
            return jsonify({"ok": False, "error": msg}), 200
        except Exception as e:
            return jsonify({"ok": False, "error": f"Network error: {e}"}), 200

        try:
            data = _json.loads(body)
        except Exception:
            return jsonify({"ok": False, "error": "Unexpected response from Bambu API."}), 200

        token = (
            data.get("token")
            or data.get("accessToken")
            or data.get("access_token")
            or (data.get("data") or {}).get("token")
            or (data.get("data") or {}).get("accessToken")
        )
        if not token:
            return jsonify({"ok": False, "error": f"Token not found in response: {list(data.keys())}"}), 200

        token_path = app.config["BAMBU_TOKEN_FILE"]
        try:
            import pathlib as _pl
            p = _pl.Path(token_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(token + "\n")
            p.chmod(0o600)
        except Exception as e:
            return jsonify({"ok": False, "error": f"Could not write token file: {e}"}), 200

        g.inv.record_audit("cloud_token.refresh", user_id=current_user.id,
                           ip_address=request.remote_addr or "")
        logger.info("Bambu Cloud token refreshed by user %s, saved to %s", current_user.username, token_path)
        return jsonify({"ok": True, "path": token_path, "length": len(token)})

    # ── Legacy redirect: /history → /jobs ────────────────────────────────────
    @app.route("/history")
    def history_redirect():
        return redirect(url_for("printers.jobs_list"))

    return app
