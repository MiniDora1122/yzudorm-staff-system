# Copyright (c) 2026 Tay Yang Long. All Rights Reserved. Designed & Developed by Tay Yang Long.
from functools import wraps

from flask import abort
from flask_login import current_user, login_required

from .models import Role


def role_required(role: Role):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not current_user.is_active or not current_user.has_role(role):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator
