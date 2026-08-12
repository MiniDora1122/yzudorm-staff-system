"""Create the first administrator after a Launcher-controlled fresh reset.

Credentials are accepted only through stdin and are never written to logs.
This script deliberately refuses to run when any user already exists.
"""

from __future__ import annotations

import base64
import json
import sys

from app import create_app
from app.extensions import db
from app.models import AuditLog, Role, User
from app.services.accounts import (
    normalize_username,
    validate_display_name,
    validate_temporary_password,
)


def decode(payload: dict[str, str], key: str) -> str:
    encoded = payload.get(key, "")
    return base64.b64decode(encoded, validate=True).decode("utf-8")


def main() -> int:
    payload = json.loads(sys.stdin.read())
    username = normalize_username(decode(payload, "username_b64"))
    display_name = validate_display_name(decode(payload, "display_name_b64"))
    password = decode(payload, "password_b64")
    validate_temporary_password(password, password)

    app = create_app()
    with app.app_context():
        existing_count = db.session.scalar(db.select(db.func.count()).select_from(User)) or 0
        if existing_count:
            raise RuntimeError("Fresh administrator bootstrap refused because users already exist.")

        administrator = User(
            username=username,
            display_name=display_name,
            role=Role.ADMIN,
            is_active=True,
            must_change_password=False,
        )
        administrator.set_password(password)
        db.session.add(administrator)
        db.session.flush()
        db.session.add(
            AuditLog(
                actor_user_id=administrator.id,
                action="SYSTEM_FRESH_INITIALIZED",
                entity_type="User",
                entity_id=administrator.id,
                safe_summary=f"全新初始化並建立第一位管理員 {username}",
            )
        )
        db.session.commit()
    print(f"BOOTSTRAP_ADMIN_CREATED={username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
