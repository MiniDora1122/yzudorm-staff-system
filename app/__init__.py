# Copyright (c) 2026 Tay Yang Long. All Rights Reserved. Designed & Developed by Tay Yang Long.
from pathlib import Path

from flask import Flask, redirect, render_template, request, session, url_for
from flask_login import current_user, logout_user
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config

from .extensions import csrf, db, login_manager, migrate, server_session
from .models import Role, User


def create_app(config_object=Config):
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    app.config.from_object(Config)
    if isinstance(config_object, dict):
        app.config.from_mapping(config_object)
    elif config_object is not Config:
        app.config.from_object(config_object)

    secret = str(app.config.get("SECRET_KEY") or "")
    unsafe_secrets = {"dev-only-change-me", "change-me", "secret", "請替換成隨機密鑰"}
    if not app.config.get("TESTING") and (len(secret) < 32 or secret.lower() in unsafe_secrets):
        raise RuntimeError(
            "SECRET_KEY is missing or unsafe. Generate a unique value with "
            "`python -c \"import secrets; print(secrets.token_hex(32))\"` and save it in .env."
        )
    if app.config.get("WEB_TRANSPORT_MODE") not in {"HTTPS", "TRUSTED_HTTP"}:
        raise RuntimeError("WEB_TRANSPORT_MODE must be HTTPS or TRUSTED_HTTP.")
    if (
        not app.config.get("TESTING")
        and app.config.get("WEB_TRANSPORT_MODE") == "HTTPS"
        and not app.config.get("SESSION_COOKIE_SECURE")
    ):
        raise RuntimeError("HTTPS mode requires SESSION_COOKIE_SECURE=1.")

    if app.config.get("TRUST_PROXY"):
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=int(app.config["PROXY_FIX_X_FOR"]),
            x_proto=int(app.config["PROXY_FIX_X_PROTO"]),
            x_host=int(app.config["PROXY_FIX_X_HOST"]),
        )

    from .services.document_keys import ensure_document_encryption_key

    ensure_document_encryption_key(app)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    server_session.init_app(app)

    login_manager.login_view = "auth.login"
    login_manager.login_message = "請先登入後再繼續。"
    login_manager.login_message_category = "warning"

    from .admin import bp as admin_bp
    from .auth import bp as auth_bp
    from .student import bp as student_bp
    from .attendance_api import bp as attendance_api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(attendance_api_bp)

    from .seed import register_commands
    from .services.backups import register_backup_commands

    register_commands(app)
    register_backup_commands(app)

    from .services.maintenance import init_maintenance_scheduler

    init_maintenance_scheduler(app)

    @app.before_request
    def reject_invalidated_session():
        user_id = session.get("_user_id")
        if user_id is None:
            return None
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            user_id = -1
        user = db.session.scalar(db.select(User).where(User.id == user_id).execution_options(populate_existing=True))
        if user is not None and user.is_active and session.get("session_version") == user.session_version:
            return None
        logout_user()
        session.clear()
        if request.endpoint == "auth.login":
            return None
        return redirect(url_for("auth.login"))

    @app.before_request
    def refresh_notifications_on_page_request():
        if current_user.is_authenticated and request.method == "GET" and request.blueprint in {"admin", "student"}:
            from .services.notifications import refresh_notifications_for_user

            refresh_notifications_for_user(current_user)

    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
        )
        if current_user.is_authenticated or request.endpoint in {"auth.login", "auth.change_password"}:
            response.headers.setdefault("Cache-Control", "no-store")
        if request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.context_processor
    def notification_navigation_context():
        if not current_user.is_authenticated:
            return {"nav_open_notification_count": 0}
        from .services.notifications import open_notification_count

        return {"nav_open_notification_count": open_notification_count(current_user)}

    @app.template_filter("localdt")
    def local_datetime_filter(value, pattern="%Y-%m-%d %H:%M"):
        from .time_utils import format_local_datetime

        return format_local_datetime(value, pattern)

    @app.get("/")
    def index():
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if current_user.role == Role.ADMIN:
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("student.dashboard"))

    @app.get("/healthz")
    def healthz():
        """Stable, lightweight endpoint for Launcher and watchdog checks."""
        try:
            db.session.execute(db.select(1)).scalar_one()
        except Exception:
            db.session.rollback()
            return {"status": "unhealthy", "service": "dorm-staff-system"}, 503
        return {"status": "ok", "service": "dorm-staff-system"}

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("errors/403.html"), 403

    return app


@login_manager.user_loader
def load_user(user_id: str):
    if not user_id.isdigit():
        return None
    user = db.session.get(User, int(user_id))
    if user is None or not user.is_active:
        return None
    if session.get("session_version") != user.session_version:
        return None
    return user
