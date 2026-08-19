from flask import Blueprint, current_app, jsonify, request

from ..extensions import csrf


bp = Blueprint("attendance_api", __name__, url_prefix="/attendance-api")
csrf.exempt(bp)


@bp.before_request
def enforce_transport():
    if not current_app.config.get("ATTENDANCE_ENABLED", True):
        return jsonify({"error": {"code": "ATTENDANCE_DISABLED", "message": "打卡服務目前已停用。"}}), 503
    mode = current_app.config.get("ATTENDANCE_TRANSPORT_MODE", "HTTPS")
    if mode not in {"HTTPS", "ENCRYPTED_HTTP"}:
        return jsonify({"error": {"code": "INVALID_SERVER_CONFIG", "message": "打卡傳輸模式設定錯誤。"}}), 503
    if mode == "HTTPS" and not current_app.testing and current_app.config.get("ATTENDANCE_REQUIRE_HTTPS") and not request.is_secure:
        return jsonify({"error": {"code": "HTTPS_REQUIRED", "message": "打卡裝置 API 僅接受 HTTPS。"}}), 426

from . import routes  # noqa: E402,F401
