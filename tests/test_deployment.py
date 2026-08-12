from pathlib import Path

from cryptography.fernet import Fernet
from flask import Flask as BaseFlask
from flask import jsonify, request

import app as app_package
from app import create_app
from deployment.create_portable_backup import portable_env, sqlite_path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_app_factory_creates_missing_instance_directory(tmp_path, monkeypatch):
    instance_path = tmp_path / "missing-instance"

    class TempInstanceFlask(BaseFlask):
        def __init__(self, *args, **kwargs):
            kwargs["instance_path"] = str(instance_path)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(app_package, "Flask", TempInstanceFlask)
    assert not instance_path.exists()
    app_package.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "instance-directory-test-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
            "DOCUMENT_STORAGE_DIR": str(tmp_path / "documents"),
            "DOCUMENT_KEY_DIR": str(tmp_path / "keys"),
            "DOCUMENT_KEY_BACKUP_DIR": str(tmp_path / "keys" / "backup"),
            "DOCUMENT_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
            "DOCUMENT_CLEANUP_SCHEDULER_ENABLED": False,
        }
    )
    assert instance_path.is_dir()


def make_proxy_app(tmp_path, trust_proxy):
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "deployment-test-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
            "DOCUMENT_STORAGE_DIR": str(tmp_path / "documents"),
            "DOCUMENT_KEY_DIR": str(tmp_path / "keys"),
            "DOCUMENT_KEY_BACKUP_DIR": str(tmp_path / "keys" / "backup"),
            "DOCUMENT_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
            "DOCUMENT_CLEANUP_SCHEDULER_ENABLED": False,
            "TRUST_PROXY": trust_proxy,
            "PROXY_FIX_X_FOR": 1,
            "PROXY_FIX_X_PROTO": 1,
            "PROXY_FIX_X_HOST": 1,
        }
    )

    @app.get("/deployment-proxy-check")
    def proxy_check():
        return jsonify(
            scheme=request.scheme,
            host=request.host,
            remote_addr=request.remote_addr,
        )

    return app


def test_proxy_headers_are_trusted_only_when_enabled(tmp_path):
    headers = {
        "X-Forwarded-For": "203.0.113.20",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "shifts.example.edu.tw",
    }
    trusted = make_proxy_app(tmp_path / "trusted", True).test_client()
    untrusted = make_proxy_app(tmp_path / "untrusted", False).test_client()

    trusted_data = trusted.get("/deployment-proxy-check", headers=headers).get_json()
    assert trusted_data == {
        "scheme": "https",
        "host": "shifts.example.edu.tw",
        "remote_addr": "203.0.113.20",
    }

    untrusted_data = untrusted.get("/deployment-proxy-check", headers=headers).get_json()
    assert untrusted_data["scheme"] == "http"
    assert untrusted_data["host"] == "localhost"
    assert untrusted_data["remote_addr"] == "127.0.0.1"


def test_runtime_templates_do_not_depend_on_external_cdn():
    template_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "app" / "templates").rglob("*.html")
    )
    assert "cdn.jsdelivr.net" not in template_text
    assert "https://" not in template_text
    assert "http://" not in template_text


def test_vendored_frontend_assets_are_present():
    expected = {
        "app/static/vendor/bootstrap/css/bootstrap.min.css": 200_000,
        "app/static/vendor/bootstrap/js/bootstrap.bundle.min.js": 70_000,
        "app/static/vendor/bootstrap-icons/bootstrap-icons.min.css": 80_000,
        "app/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2": 100_000,
        "app/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff": 150_000,
        "app/static/vendor/fullcalendar/index.global.min.js": 250_000,
    }
    for relative, minimum_size in expected.items():
        asset = PROJECT_ROOT / relative
        assert asset.is_file(), relative
        assert asset.stat().st_size >= minimum_size, relative

    icon_css = (
        PROJECT_ROOT / "app/static/vendor/bootstrap-icons/bootstrap-icons.min.css"
    ).read_text(encoding="utf-8")
    assert 'url("fonts/bootstrap-icons.woff2' in icon_css
    assert 'url("https://' not in icon_css
    assert "url('https://" not in icon_css


def test_deployment_files_are_available():
    expected = [
        "deployment/start-production.ps1",
        "deployment/install-production.ps1",
        "deployment/register-startup-task.ps1",
        "deployment/unregister-startup-task.ps1",
        "deployment/create-portable-backup.ps1",
        "deployment/create_portable_backup.py",
        "deployment/restore-portable.ps1",
        "deployment/update-from-git.ps1",
        "deployment/GIT_UPDATE_GUIDE.md",
        "deployment/apache/dorm-staff-vhost.conf.example",
        "deployment/DEPLOYMENT_WINDOWS_XAMPP.md",
    ]
    for relative in expected:
        assert (PROJECT_ROOT / relative).is_file(), relative


def test_launcher_persists_secret_key_recovery_backup():
    source = (PROJECT_ROOT / "portable-windows-launcher" / "DormStaffLauncher.cs").read_text(
        encoding="utf-8"
    )
    assert '"application-env.backup"' in source
    assert "BackupEnvironmentFile(created);" in source
    assert "BackupEnvironmentFile(true);" in source
    assert "HashesEqual(sourceHash, backupHash)" in source
    assert "File.SetAttributes(backupPath, FileAttributes.Normal)" in source


def test_launcher_includes_safe_data_migration_controls():
    launcher_root = PROJECT_ROOT / "portable-windows-launcher"
    source = (launcher_root / "DormStaffLauncher.cs").read_text(encoding="utf-8")
    assert (launcher_root / "migrate_portable_data.py").is_file()
    assert "InspectMigrationSource" in source
    assert "RestorePortableData" in source
    assert "ExportPortableBackup" in source
    assert 'phraseBox.Text != "MIGRATE"' in source


def test_gitignore_protects_production_data():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    required_rules = [".env", "instance/", "outputs/", "tmp/", "*.db", "*.zip"]
    for rule in required_rules:
        assert rule in gitignore
    assert "!.env.example" in gitignore
    assert "!.env.production.example" in gitignore


def test_portable_backup_uses_flask_instance_database_location():
    assert sqlite_path("sqlite:///dorm_staff.db") == (
        PROJECT_ROOT / "instance" / "dorm_staff.db"
    ).resolve()
    rendered = portable_env({"SECRET_KEY": "keep-this-secret"})
    assert "DATABASE_URL=sqlite:///dorm_staff.db" in rendered
    assert "SECRET_KEY=keep-this-secret" in rendered
