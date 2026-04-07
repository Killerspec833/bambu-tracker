from __future__ import annotations

"""
Shared Flask-Limiter instance.

Created here so it can be imported by both web_ui.py (init_app)
and auth_bp.py (rate-limit decorator).
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
