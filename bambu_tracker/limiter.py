from __future__ import annotations

"""
Shared Flask-Limiter instance.

Created here so it can be imported by both web_ui.py (init_app)
and auth_bp.py (rate-limit decorator).

Storage backend is selected via the RATELIMIT_STORAGE_URI env var:
  - Not set → in-memory (dev only; resets on restart)
  - redis://redis:6379 → Redis (recommended for production / multi-worker)
"""

import os

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

_storage_uri = os.environ.get("RATELIMIT_STORAGE_URI")

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=_storage_uri,
)
