from __future__ import annotations

"""
Authentication layer: Flask-Login integration + bcrypt helpers.

User rows are plain dicts; the User class wraps them for Flask-Login.
"""

import logging
from datetime import datetime, timezone
from typing import Any

import bcrypt
from flask_login import LoginManager, UserMixin
from sqlalchemy import select, update

from .db import get_engine, users

logger = logging.getLogger(__name__)

login_manager = LoginManager()
login_manager.login_view = "auth.login"  # type: ignore[assignment]
login_manager.login_message = "Please log in to continue."


class User(UserMixin):
    """Thin wrapper around a users row dict for Flask-Login."""

    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    # Flask-Login interface
    def get_id(self) -> str:
        return str(self._row["id"])

    @property
    def is_active(self) -> bool:
        return bool(self._row.get("is_active", True))

    # Convenience properties
    @property
    def id(self) -> int:
        return int(self._row["id"])

    @property
    def username(self) -> str:
        return str(self._row["username"])

    @property
    def email(self) -> str:
        return str(self._row["email"])

    @property
    def role(self) -> str:
        return str(self._row.get("role", "operator"))

    def is_admin(self) -> bool:
        return self.role == "admin"

    def can_write(self) -> bool:
        return self.role in ("admin", "operator")


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    try:
        uid = int(user_id)
    except (ValueError, TypeError):
        return None
    with get_engine().connect() as conn:
        row = conn.execute(select(users).where(users.c.id == uid)).mappings().fetchone()
    return User(dict(row)) if row else None


# ─── helpers ──────────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def check_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def get_user_by_username(username: str) -> User | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(users).where(users.c.username == username)
        ).mappings().fetchone()
    return User(dict(row)) if row else None


def record_login(user_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(users)
            .where(users.c.id == user_id)
            .values(last_login_at=datetime.now(timezone.utc))
        )


def create_user(
    username: str,
    email: str,
    password: str,
    role: str = "operator",
) -> int:
    """Insert a new user. Returns the new user id."""
    now = datetime.now(timezone.utc)
    from sqlalchemy import insert as _ins
    with get_engine().begin() as conn:
        result = conn.execute(
            _ins(users).values(
                username=username,
                email=email,
                password_hash=hash_password(password),
                role=role,
                is_active=True,
                created_at=now,
            ).returning(users.c.id)
        )
        row = result.fetchone()
    return int(row[0])


def list_users() -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(
                users.c.id,
                users.c.username,
                users.c.email,
                users.c.role,
                users.c.is_active,
                users.c.created_at,
                users.c.last_login_at,
            ).order_by(users.c.username)
        ).mappings().fetchall()
    return [dict(r) for r in rows]


def set_user_active(user_id: int, active: bool) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(users).where(users.c.id == user_id).values(is_active=active)
        )
