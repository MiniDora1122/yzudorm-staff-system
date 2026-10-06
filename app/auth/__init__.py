# Copyright (c) 2026 Tay Yang Long. All Rights Reserved. Designed & Developed by Tay Yang Long.
from flask import Blueprint


bp = Blueprint("auth", __name__, url_prefix="/auth")

from . import routes  # noqa: E402, F401
