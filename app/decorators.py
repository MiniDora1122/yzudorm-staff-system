from functools import wraps

from flask import abort
from flask_login import current_user, login_required

from .models import Role


def role_required(role: Role):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not current_user.has_role(role):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator

