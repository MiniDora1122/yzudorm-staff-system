from pathlib import Path
import time

from flask import Flask, redirect, render_template, request, session, url_for
from flask_login import current_user
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

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(student_bp)

    from .seed import register_commands
    from .services.backups import register_backup_commands

    register_commands(app)
    register_backup_commands(app)

    from .services.maintenance import init_maintenance_scheduler

    init_maintenance_scheduler(app)

    @app.before_request
    def refresh_action_notifications():
        if current_user.is_authenticated and request.endpoint != "static":
            now = int(time.time())
            last_sync = int(session.get("notification_sync_at", 0))
            if now - last_sync < int(app.config["NOTIFICATION_SYNC_INTERVAL_SECONDS"]):
                return
            from .services.notifications import sync_admin_notifications, sync_student_notifications

            if current_user.role == Role.ADMIN:
                sync_admin_notifications()
            else:
                sync_student_notifications(current_user)
            session["notification_sync_at"] = now

    @app.after_request
    def invalidate_notifications_after_mutation(response):
        if current_user.is_authenticated and request.method not in {"GET", "HEAD", "OPTIONS"}:
            session.pop("notification_sync_at", None)
        return response

    @app.context_processor
    def notification_navigation_context():
        if not current_user.is_authenticated:
            return {"nav_open_notification_count": 0}
        from .services.notifications import open_notification_count

        return {"nav_open_notification_count": open_notification_count(current_user)}

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
    return db.session.get(User, int(user_id))
