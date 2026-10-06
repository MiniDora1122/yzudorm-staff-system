# Copyright (c) 2026 Tay Yang Long. All Rights Reserved. Designed & Developed by Tay Yang Long.
from flask import Blueprint


bp = Blueprint("student", __name__, url_prefix="/student")

from . import routes  # noqa: E402, F401
from . import attendance, vacancies  # noqa: E402, F401
