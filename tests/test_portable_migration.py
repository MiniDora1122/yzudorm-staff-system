import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from cryptography.fernet import Fernet


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = PROJECT_ROOT / "portable-windows-launcher" / "migrate_portable_data.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("portable_migration_helper", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


helper = load_helper()


def latest_revision() -> str:
    revisions = set()
    parents = set()
    for migration in (PROJECT_ROOT / "migrations" / "versions").glob("*.py"):
        text = migration.read_text(encoding="utf-8")
        revision = helper.re.search(r"^revision\s*=\s*['\"]([^'\"]+)", text, helper.re.MULTILINE)
        parent = helper.re.search(r"^down_revision\s*=\s*['\"]([^'\"]+)", text, helper.re.MULTILINE)
        if revision:
            revisions.add(revision.group(1))
        if parent:
            parents.add(parent.group(1))
    heads = revisions - parents
    assert len(heads) == 1
    return heads.pop()


def make_minimal_database(path: Path, revision: str, documents: int = 0):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL);
            CREATE TABLE users (id INTEGER PRIMARY KEY);
            CREATE TABLE staff_profiles (id INTEGER PRIMARY KEY);
            CREATE TABLE shifts (id INTEGER PRIMARY KEY);
            CREATE TABLE staff_documents (id INTEGER PRIMARY KEY);
            """
        )
        connection.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
        connection.execute("INSERT INTO users DEFAULT VALUES")
        connection.execute("INSERT INTO staff_profiles DEFAULT VALUES")
        connection.execute("INSERT INTO shifts DEFAULT VALUES")
        for _ in range(documents):
            connection.execute("INSERT INTO staff_documents DEFAULT VALUES")
        connection.commit()


def write_portable_zip(path: Path, files: dict[str, bytes]):
    manifest_files = {
        name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in files.items()
    }
    manifest = {
        "format": helper.BACKUP_FORMAT,
        "created_at_utc": "2026-08-12T00:00:00+00:00",
        "file_count": len(files),
        "files": manifest_files,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr("PORTABLE_BACKUP_MANIFEST.json", json.dumps(manifest).encode("utf-8"))


def test_inspect_database_reports_counts_and_document_warning(tmp_path):
    database = tmp_path / "source.db"
    make_minimal_database(database, latest_revision(), documents=2)

    info = helper.inspect_database(database, PROJECT_ROOT, "DB")

    assert info.users == 1
    assert info.staff == 1
    assert info.shifts == 1
    assert info.documents == 2
    assert "完整備份 ZIP" in info.warning


def test_inspect_rejects_unknown_newer_revision(tmp_path):
    database = tmp_path / "future.db"
    make_minimal_database(database, "future_revision")

    with pytest.raises(helper.MigrationError, match="請先更新"):
        helper.inspect_database(database, PROJECT_ROOT, "DB")


def test_full_zip_requires_manifest_and_document_key(tmp_path):
    database = tmp_path / "source.db"
    make_minimal_database(database, latest_revision(), documents=1)
    archive = tmp_path / "source.zip"
    write_portable_zip(
        archive,
        {
            ".env": b"SECRET_KEY=portable-zip-test-secret-at-least-32-characters\nDATABASE_URL=sqlite:///dorm_staff.db\n",
            "instance/dorm_staff.db": database.read_bytes(),
            "instance/private_keys/document-fernet.key": Fernet.generate_key() + b"\n",
        },
    )

    info = helper.inspect_zip(archive, PROJECT_ROOT)

    assert info.source_type == "ZIP"
    assert info.documents == 1
    assert not info.warning


def test_archive_rejects_path_traversal(tmp_path):
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../outside.txt", "unsafe")

    with zipfile.ZipFile(archive_path) as archive, pytest.raises(helper.MigrationError, match="不安全路徑"):
        helper.archive_manifest(archive)


def test_locked_target_is_never_deleted_when_initial_rename_fails(tmp_path, monkeypatch):
    project = tmp_path / "target"
    (project / "instance").mkdir(parents=True)
    shutil.copytree(PROJECT_ROOT / "migrations", project / "migrations")
    (project / ".env").write_text("DATABASE_URL=sqlite:///dorm_staff.db\n", encoding="utf-8")
    target = project / "instance" / "dorm_staff.db"
    source = tmp_path / "source.db"
    make_minimal_database(target, latest_revision())
    make_minimal_database(source, latest_revision())
    original_bytes = target.read_bytes()
    original_rename = Path.rename

    def locked_rename(path, destination):
        if path.resolve() == target.resolve():
            raise PermissionError("simulated database lock")
        return original_rename(path, destination)

    monkeypatch.setattr(helper, "create_target_backup", lambda _root: None)
    monkeypatch.setattr(Path, "rename", locked_rename)

    with pytest.raises(helper.MigrationError, match="已嘗試回復"):
        helper.restore_source(source, project)

    assert target.read_bytes() == original_bytes
    assert not list(project.glob(".dorm-migration-staging-*"))


def copy_runtime_project(destination: Path):
    for directory in ("app", "migrations", "deployment"):
        shutil.copytree(PROJECT_ROOT / directory, destination / directory)
    for filename in ("config.py", "wsgi.py"):
        shutil.copy2(PROJECT_ROOT / filename, destination / filename)
    (destination / ".env").write_text(
        "\n".join(
            [
                "FLASK_APP=wsgi.py",
                "SECRET_KEY=portable-migration-integration-secret",
                "DATABASE_URL=sqlite:///dorm_staff.db",
                "SESSION_TYPE=cachelib",
                "SESSION_COOKIE_SECURE=0",
                "TRUST_PROXY=0",
                "DOCUMENT_STORAGE_DIR=instance/private_documents",
                "DOCUMENT_KEY_DIR=instance/private_keys",
                "DOCUMENT_KEY_BACKUP_DIR=instance/private_keys/backup",
                "DOCUMENT_ENCRYPTION_KEY=",
                "DOCUMENT_CLEANUP_SCHEDULER_ENABLED=0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def run_in_project(project: Path, *arguments: str):
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment.pop("DATABASE_URL", None)
    return subprocess.run(
        [sys.executable, *arguments],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def create_runtime_project(project: Path, username: str):
    project.mkdir()
    copy_runtime_project(project)
    run_in_project(project, "-m", "flask", "--app", "wsgi.py", "db", "upgrade")
    create_admin = (
        "from app import create_app; from app.extensions import db; from app.models import User,Role; "
        "a=create_app(); c=a.app_context(); c.push(); "
        f"u=User(username={username!r},display_name='Admin',role=Role.ADMIN,is_active=True); "
        "u.set_password('OldAdmin!2026'); db.session.add(u); db.session.commit(); c.pop()"
    )
    run_in_project(project, "-X", "utf8", "-c", create_admin)


def test_database_restore_replaces_target_and_keeps_rollback_backup(tmp_path):
    project = tmp_path / "target"
    create_runtime_project(project, "old-admin")

    target_database = project / "instance" / "dorm_staff.db"
    source_database = tmp_path / "source.db"
    shutil.copy2(target_database, source_database)
    with sqlite3.connect(source_database) as connection:
        connection.execute("UPDATE users SET username='migrated-admin' WHERE username='old-admin'")
        connection.commit()

    info, backup = helper.restore_source(source_database, project)

    assert info.source_type == "DB"
    assert source_database.is_file()
    assert backup and backup.is_file()
    assert not list(project.glob(".dorm-migration-staging-*"))
    with sqlite3.connect(target_database) as connection:
        assert connection.execute("SELECT username FROM users").fetchone()[0] == "migrated-admin"
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action='PORTABLE_DATA_RESTORE'"
        ).fetchone()[0] == 1
    env_backup = project / "instance" / "private_keys" / "backup" / "application-env.backup"
    assert env_backup.read_bytes() == (project / ".env").read_bytes()


def test_failed_restore_rolls_back_original_database(tmp_path):
    project = tmp_path / "target"
    create_runtime_project(project, "safe-admin")
    target_database = project / "instance" / "dorm_staff.db"
    source_database = tmp_path / "source-without-admin.db"
    shutil.copy2(target_database, source_database)
    with sqlite3.connect(source_database) as connection:
        connection.execute("DELETE FROM users")
        connection.commit()

    with pytest.raises(helper.MigrationError, match="已嘗試回復"):
        helper.restore_source(source_database, project)

    with sqlite3.connect(target_database) as connection:
        assert connection.execute("SELECT username FROM users").fetchone()[0] == "safe-admin"
    assert source_database.is_file()
    assert not list(project.glob(".dorm-migration-staging-*"))
    assert list((project / "outputs" / "portable-backups").glob("before-data-migration-*.zip"))


def test_full_zip_restore_uses_data_only_and_preserves_current_code(tmp_path):
    source_project = tmp_path / "source-project"
    target_project = tmp_path / "target-project"
    create_runtime_project(source_project, "zip-admin")
    create_runtime_project(target_project, "target-admin")
    marker = target_project / "CURRENT_CODE_MARKER.txt"
    marker.write_text("keep current code", encoding="utf-8")
    archive = tmp_path / "portable-source.zip"
    run_in_project(
        source_project,
        str(source_project / "deployment" / "create_portable_backup.py"),
        str(archive),
        "--allow-running",
    )
    with zipfile.ZipFile(archive) as portable_backup:
        assert "wsgi.py" not in portable_backup.namelist()
        assert "instance/dorm_staff.db" in portable_backup.namelist()

    info, backup = helper.restore_source(archive, target_project)

    assert info.source_type == "ZIP"
    assert backup and backup.is_file()
    assert marker.read_text(encoding="utf-8") == "keep current code"
    with sqlite3.connect(target_project / "instance" / "dorm_staff.db") as connection:
        assert connection.execute("SELECT username FROM users").fetchone()[0] == "zip-admin"
    assert archive.is_file()
