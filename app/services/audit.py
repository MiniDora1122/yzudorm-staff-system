# Copyright (c) 2026 Tay Yang Long. All Rights Reserved. Designed & Developed by Tay Yang Long.
from __future__ import annotations

import json

from flask import has_request_context, request

from ..extensions import db
from ..models import AuditLog


def add_audit(
    actor_user_id: int | None,
    action: str,
    entity_type: str,
    entity_id: int,
    summary: str,
    *,
    changes: dict | None = None,
) -> None:
    request_data = {}
    if has_request_context():
        request_data = {
            "ip_address": (request.remote_addr or "")[:45] or None,
            "user_agent": request.user_agent.string[:500] or None,
            "http_method": request.method[:10],
            "route": (request.endpoint or request.path)[:255],
        }
    db.session.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            safe_summary=summary[:500],
            changes_json=(json.dumps(changes, ensure_ascii=False, default=str)[:4000] if changes else None),
            **request_data,
        )
    )
