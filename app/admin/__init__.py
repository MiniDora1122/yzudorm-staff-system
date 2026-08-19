from flask import Blueprint


bp = Blueprint("admin", __name__, url_prefix="/admin")

from . import routes  # noqa: E402, F401
from . import attendance, operations, workforce  # noqa: E402, F401
