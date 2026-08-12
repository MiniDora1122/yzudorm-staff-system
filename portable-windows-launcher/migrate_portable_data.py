"""Validate and safely replace portable dorm-staff data.

The launcher calls this helper with explicit project and source paths.  It never
merges databases: the current target is backed up, then replaced transactionally
with rollback files kept on the same volume until validation succeeds.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path, PurePosixPath

from cryptography.fernet import Fernet, InvalidToken
from dotenv import dotenv_values


BACKUP_FORMAT = "dorm-staff-portable-backup-v1"
REQUIRED_TABLES = {"alembic_version", "users", "staff_profiles", "shifts"}
MAX_ARCHIVE_FILES = 100_000
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024 * 1024


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceInfo:
    source_type: str
    revision: str
    users: int
    staff: int
    shifts: int
    documents: int
    warning: str = ""

    @property
    def summary(self) -> str:
        source_label = "完整備份 ZIP / Full backup ZIP" if self.source_type == "ZIP" else "單一 SQLite / Database only"
        lines = [
            f"來源類型：{source_label}",
            f"Schema revision：{self.revision}",
            f"帳號：{self.users}｜工讀生：{self.staff}｜排班：{self.shifts}｜證件紀錄：{self.documents}",
        ]
        if self.warning:
            lines.extend(["", "警告：" + self.warning])
        return "\n".join(lines)


def b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def emit_info(info: SourceInfo) -> None:
    print(f"MIGRATION_SOURCE_TYPE={info.source_type}")
    print(f"MIGRATION_REVISION={info.revision}")
    print(f"MIGRATION_USERS={info.users}")
    print(f"MIGRATION_STAFF={info.staff}")
    print(f"MIGRATION_SHIFTS={info.shifts}")
    print(f"MIGRATION_DOCUMENTS={info.documents}")
    print(f"MIGRATION_SUMMARY_B64={b64(info.summary)}")


def known_revisions(project_root: Path) -> set[str]:
    revisions: set[str] = set()
    pattern = re.compile(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
    for migration in (project_root / "migrations" / "versions").glob("*.py"):
        match = pattern.search(migration.read_text(encoding="utf-8"))
        if match:
            revisions.add(match.group(1))
    if not revisions:
        raise MigrationError("目前程式找不到 migration revision，無法驗證來源資料庫。")
    return revisions


def sqlite_connection(path: Path, read_only: bool = True) -> sqlite3.Connection:
    if read_only:
        uri = path.resolve().as_uri() + "?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=10)
    return sqlite3.connect(path, timeout=10)


def table_count(connection: sqlite3.Connection, table: str) -> int:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not exists:
        return 0
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def inspect_database(path: Path, project_root: Path, source_type: str) -> SourceInfo:
    if not path.is_file():
        raise MigrationError(f"找不到來源資料庫：{path}")
    with path.open("rb") as source_file:
        header = source_file.read(16)
    if path.stat().st_size < 100 or header != b"SQLite format 3\x00":
        raise MigrationError("選取的檔案不是有效的 SQLite 資料庫。")
    try:
        with closing(sqlite_connection(path)) as connection:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
            if quick_check != "ok":
                raise MigrationError("來源 SQLite 完整性檢查失敗。")
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            missing = REQUIRED_TABLES - tables
            if missing:
                raise MigrationError("來源不是可支援的宿舍工讀生系統資料庫；缺少資料表：" + ", ".join(sorted(missing)))
            revision_rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
            if len(revision_rows) != 1 or not revision_rows[0][0]:
                raise MigrationError("來源資料庫沒有可辨識的單一 migration revision。")
            revision = str(revision_rows[0][0])
            if revision not in known_revisions(project_root):
                raise MigrationError(
                    "來源資料庫 revision 比目前程式新或不受支援（"
                    + revision
                    + "）；請先更新 Launcher 與系統程式。"
                )
            documents = table_count(connection, "staff_documents")
            warning = ""
            if source_type == "DB" and documents:
                warning = (
                    f"此資料庫有 {documents} 筆證件紀錄，但單一 DB 不含證件檔案與解密金鑰；"
                    "移轉後相關文件可能無法下載。建議改用完整備份 ZIP。"
                )
            return SourceInfo(
                source_type=source_type,
                revision=revision,
                users=table_count(connection, "users"),
                staff=table_count(connection, "staff_profiles"),
                shifts=table_count(connection, "shifts"),
                documents=documents,
                warning=warning,
            )
    except sqlite3.DatabaseError as exc:
        raise MigrationError("無法讀取來源 SQLite；檔案可能損毀或仍被不相容程式鎖定。") from exc


def safe_archive_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or normalized.startswith("/") or ".." in path.parts or ":" in path.parts[0]:
        raise MigrationError("備份 ZIP 包含不安全路徑，已拒絕處理。")
    return path.as_posix()


def archive_manifest(archive: zipfile.ZipFile) -> tuple[dict, dict[str, zipfile.ZipInfo]]:
    entries: dict[str, zipfile.ZipInfo] = {}
    total_size = 0
    for entry in archive.infolist():
        name = safe_archive_name(entry.filename)
        mode = (entry.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise MigrationError("備份 ZIP 不可包含 symbolic link。")
        if name in entries:
            raise MigrationError("備份 ZIP 包含重複路徑：" + name)
        entries[name] = entry
        total_size += entry.file_size
    if len(entries) > MAX_ARCHIVE_FILES or total_size > MAX_ARCHIVE_BYTES:
        raise MigrationError("備份 ZIP 展開後過大或檔案數異常，已拒絕處理。")
    required = {"PORTABLE_BACKUP_MANIFEST.json", ".env", "instance/dorm_staff.db"}
    missing = required - entries.keys()
    if missing:
        raise MigrationError("不是完整 portable backup；缺少：" + ", ".join(sorted(missing)))
    manifest_entry = entries["PORTABLE_BACKUP_MANIFEST.json"]
    if manifest_entry.file_size > 10 * 1024 * 1024:
        raise MigrationError("備份 manifest 大小異常。")
    try:
        manifest = json.loads(archive.read(manifest_entry).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationError("備份 manifest 無法解析。") from exc
    if manifest.get("format") != BACKUP_FORMAT or not isinstance(manifest.get("files"), dict):
        raise MigrationError("不支援的 portable backup 格式。")
    return manifest, entries


def verify_archive_entry(archive: zipfile.ZipFile, entry: zipfile.ZipInfo, expected: dict) -> None:
    digest = hashlib.sha256()
    size = 0
    with archive.open(entry) as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    if size != expected.get("size") or digest.hexdigest().lower() != str(expected.get("sha256", "")).lower():
        raise MigrationError("備份檔案驗證失敗：" + entry.filename)


def validate_archive_environment(data: bytes) -> None:
    try:
        values = dotenv_values(stream=StringIO(data.decode("utf-8-sig")))
    except UnicodeDecodeError as exc:
        raise MigrationError("備份內的 .env 不是有效 UTF-8。") from exc
    secret = str(values.get("SECRET_KEY") or "")
    if len(secret) < 32 or "請替換" in secret or secret == "dev-only-change-me":
        raise MigrationError("備份內缺少安全的 SECRET_KEY，為避免還原後 session 不安全已拒絕處理。")


def inspect_zip(path: Path, project_root: Path) -> SourceInfo:
    if not path.is_file() or not zipfile.is_zipfile(path):
        raise MigrationError("選取的檔案不是有效的 portable backup ZIP。")
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory(prefix="dorm-migration-inspect-") as temporary:
        manifest, entries = archive_manifest(archive)
        manifest_files = manifest["files"]
        for name in (".env", "instance/dorm_staff.db"):
            if name not in manifest_files:
                raise MigrationError("備份 manifest 缺少必要檔案：" + name)
            verify_archive_entry(archive, entries[name], manifest_files[name])
        validate_archive_environment(archive.read(entries[".env"]))
        database = Path(temporary) / "source.db"
        with archive.open(entries["instance/dorm_staff.db"]) as source, database.open("wb") as target:
            shutil.copyfileobj(source, target)
        info = inspect_database(database, project_root, "ZIP")
        if info.documents and not (
            "instance/private_keys/document-fernet.key" in entries
            or "instance/private_keys/backup/document-fernet.key" in entries
        ):
            raise MigrationError("備份含證件紀錄但缺少文件解密金鑰，為避免無法下載證件已拒絕還原。")
        return info


def inspect_source(source: Path, project_root: Path) -> SourceInfo:
    suffix = source.suffix.lower()
    if suffix == ".zip":
        return inspect_zip(source, project_root)
    if suffix not in {".db", ".sqlite", ".sqlite3"}:
        raise MigrationError("來源只接受 portable backup ZIP 或 SQLite DB。")
    return inspect_database(source, project_root, "DB")


def sqlite_snapshot(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite_connection(source)) as source_connection:
        with closing(sqlite3.connect(destination)) as target_connection:
            source_connection.backup(target_connection)


def extract_data_archive(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(source) as archive:
        manifest, entries = archive_manifest(archive)
        manifest_files: dict = manifest["files"]
        selected = [
            name
            for name in entries
            if name == ".env" or name == "instance/dorm_staff.db" or name.startswith("instance/private_documents/") or name.startswith("instance/private_keys/")
        ]
        selected_size = sum(entries[name].file_size for name in selected)
        required_space = selected_size * 2 + 256 * 1024 * 1024
        if shutil.disk_usage(destination.parent).free < required_space:
            raise MigrationError("目的磁碟空間不足，無法安全保留暫存資料與回復空間。")
        for name in selected:
            if name not in manifest_files:
                raise MigrationError("敏感資料未列入備份 manifest：" + name)
            verify_archive_entry(archive, entries[name], manifest_files[name])
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entries[name]) as input_file, target.open("wb") as output_file:
                shutil.copyfileobj(input_file, output_file)
        validate_archive_environment((destination / ".env").read_bytes())


def normalize_portable_env(path: Path) -> None:
    replacements = {
        "DATABASE_URL": "sqlite:///dorm_staff.db",
        "DOCUMENT_STORAGE_DIR": "instance/private_documents",
        "DOCUMENT_KEY_DIR": "instance/private_keys",
        "DOCUMENT_KEY_BACKUP_DIR": "instance/private_keys/backup",
    }
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    found: set[str] = set()
    normalized: list[str] = []
    for line in lines:
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match.group(1) in replacements:
            key = match.group(1)
            normalized.append(f"{key}={replacements[key]}")
            found.add(key)
        else:
            normalized.append(line)
    for key, value in replacements.items():
        if key not in found:
            normalized.append(f"{key}={value}")
    path.write_text("\n".join(normalized) + "\n", encoding="utf-8")


def target_database(project_root: Path) -> Path:
    env_path = project_root / ".env"
    if not env_path.is_file():
        raise MigrationError("目的系統缺少 .env，請先執行安裝／修復環境。")
    database_url = dotenv_values(env_path).get("DATABASE_URL") or "sqlite:///dorm_staff.db"
    prefix = "sqlite:///"
    if not database_url.startswith(prefix) or database_url == "sqlite:///:memory:":
        raise MigrationError("資料移轉目前只支援 portable SQLite 目的系統。")
    raw = database_url[len(prefix) :]
    raw = raw.lstrip("/") if len(raw) > 2 and raw[1:3] == ":/" else raw
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = project_root / "instance" / candidate
    candidate = candidate.resolve()
    instance_root = (project_root / "instance").resolve()
    if not candidate.is_relative_to(instance_root):
        raise MigrationError("為避免覆蓋外部資料，目的 SQLite 必須位於專案 instance 內。")
    return candidate


def create_target_backup(project_root: Path) -> Path | None:
    database = target_database(project_root)
    if not database.is_file():
        return None
    script = project_root / "deployment" / "create_portable_backup.py"
    if not script.is_file():
        raise MigrationError("找不到移轉前備份程式，已停止操作。")
    destination = project_root / "outputs" / "portable-backups" / (
        "before-data-migration-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(script), str(destination), "--allow-running"],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if result.returncode != 0 or not destination.is_file():
        raise MigrationError("移轉前完整備份失敗，未修改任何資料。\n" + (result.stderr or result.stdout)[-2000:])
    return destination


def project_environment(project_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    for key, value in dotenv_values(project_root / ".env").items():
        if value is not None:
            environment[key] = value
    return environment


def run_migrations(project_root: Path) -> None:
    environment = project_environment(project_root)
    result = subprocess.run(
        [sys.executable, "-m", "flask", "--app", "wsgi.py", "db", "upgrade"],
        cwd=project_root,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if result.returncode != 0:
        raise MigrationError("來源資料庫 migration 失敗。\n" + (result.stderr or result.stdout)[-4000:])


def application_health_check(project_root: Path, source_type: str) -> None:
    code = (
        "from app import create_app; from app.extensions import db; from app.models import User,Role; "
        "a=create_app(); c=a.app_context(); c.push(); "
        "n=db.session.scalar(db.select(db.func.count()).select_from(User).where(User.role==Role.ADMIN,User.is_active.is_(True))) or 0; "
        "assert n>0, 'No active administrator'; print('ACTIVE_ADMINS='+str(n)); c.pop()"
    )
    environment = project_environment(project_root)
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code],
        cwd=project_root,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if result.returncode != 0:
        raise MigrationError("移轉後應用程式驗證失敗。\n" + (result.stderr or result.stdout)[-3000:])
    database = target_database(project_root)
    inspect_database(database, project_root, source_type)


def verify_full_documents(project_root: Path) -> None:
    database = target_database(project_root)
    with closing(sqlite_connection(database)) as connection:
        rows = connection.execute(
            "SELECT storage_key FROM staff_documents WHERE storage_key IS NOT NULL LIMIT 4"
        ).fetchall()
    if not rows:
        return
    key_paths = [
        project_root / "instance" / "private_keys" / "document-fernet.key",
        project_root / "instance" / "private_keys" / "backup" / "document-fernet.key",
    ]
    key_path = next((path for path in key_paths if path.is_file()), None)
    if key_path is None:
        raise MigrationError("移轉後缺少文件解密金鑰。")
    try:
        fernet = Fernet(key_path.read_bytes().strip())
        for (storage_key,) in rows:
            document = (project_root / "instance" / "private_documents" / storage_key).resolve()
            root = (project_root / "instance" / "private_documents").resolve()
            if not document.is_relative_to(root) or not document.is_file():
                raise MigrationError("移轉後有證件檔案遺失，已停止還原。")
            fernet.decrypt(document.read_bytes())
    except (ValueError, InvalidToken) as exc:
        raise MigrationError("移轉後證件金鑰無法解密文件，已停止還原。") from exc


def append_migration_audit(project_root: Path, source_type: str) -> None:
    with closing(sqlite_connection(target_database(project_root), read_only=False)) as connection:
        connection.execute(
            "INSERT INTO audit_logs "
            "(actor_user_id, action, entity_type, entity_id, safe_summary, ip_address, user_agent, http_method, route, created_at) "
            "VALUES (NULL, ?, 'SYSTEM', 0, ?, NULL, 'DormStaffLauncher', NULL, NULL, ?)",
            (
                "PORTABLE_DATA_RESTORE",
                "Launcher restored a full backup ZIP." if source_type == "ZIP" else "Launcher replaced the database from SQLite source.",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()


def refresh_environment_backup(project_root: Path) -> None:
    source = project_root / ".env"
    destination = project_root / "instance" / "private_keys" / "backup" / "application-env.backup"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp-" + uuid.uuid4().hex)
    try:
        shutil.copy2(source, temporary)
        if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(temporary.read_bytes()).digest():
            raise MigrationError("移轉後的 .env 復原備份驗證失敗。")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def restore_source(source: Path, project_root: Path) -> tuple[SourceInfo, Path | None]:
    project_root = project_root.resolve()
    source = source.resolve()
    info = inspect_source(source, project_root)
    database = target_database(project_root)
    if info.source_type == "DB" and source == database:
        raise MigrationError("來源與目的資料庫相同，不需要移轉。")
    backup = create_target_backup(project_root)
    staging = project_root / (".dorm-migration-staging-" + uuid.uuid4().hex)
    payload = staging / "payload"
    old_instance = staging / "instance-old"
    old_database = staging / "database-old.db"
    old_env = staging / "env-old"
    instance = project_root / "instance"
    env_path = project_root / ".env"
    staging.mkdir()
    moved_instance = False
    moved_database = False
    replacing_instance_started = False
    replacing_database_started = False
    try:
        payload.mkdir()
        if info.source_type == "ZIP":
            extract_data_archive(source, payload)
            normalize_portable_env(payload / ".env")
            inspect_database(payload / "instance" / "dorm_staff.db", project_root, "ZIP")
            shutil.copy2(env_path, old_env)
            if instance.exists():
                instance.rename(old_instance)
                moved_instance = True
            replacing_instance_started = True
            shutil.copytree(payload / "instance", instance)
            shutil.copy2(payload / ".env", env_path)
        else:
            staged_database = payload / "dorm_staff.db"
            if shutil.disk_usage(payload).free < source.stat().st_size * 2 + 256 * 1024 * 1024:
                raise MigrationError("目的磁碟空間不足，無法安全移轉 SQLite。")
            sqlite_snapshot(source, staged_database)
            inspect_database(staged_database, project_root, "DB")
            database.parent.mkdir(parents=True, exist_ok=True)
            if database.exists():
                database.rename(old_database)
                moved_database = True
            replacing_database_started = True
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(database) + suffix).unlink(missing_ok=True)
            shutil.copy2(staged_database, database)

        run_migrations(project_root)
        application_health_check(project_root, info.source_type)
        if info.source_type == "ZIP":
            verify_full_documents(project_root)
        refresh_environment_backup(project_root)
        append_migration_audit(project_root, info.source_type)
    except Exception as restore_error:
        rollback_error = ""
        try:
            if info.source_type == "ZIP":
                if replacing_instance_started and instance.exists():
                    shutil.rmtree(instance)
                if moved_instance and old_instance.exists():
                    old_instance.rename(instance)
                if old_env.exists():
                    shutil.copy2(old_env, env_path)
            else:
                if replacing_database_started:
                    database.unlink(missing_ok=True)
                    for suffix in ("-wal", "-shm", "-journal"):
                        Path(str(database) + suffix).unlink(missing_ok=True)
                if moved_database and old_database.exists():
                    old_database.rename(database)
        except Exception as rollback_exception:  # pragma: no cover - catastrophic filesystem failure
            rollback_error = "\n自動回復也失敗，請保留暫存目錄並聯絡系統管理者：" + str(rollback_exception)
        if not rollback_error:
            shutil.rmtree(staging, ignore_errors=True)
        raise MigrationError("資料移轉失敗；已嘗試回復原系統。\n" + str(restore_error) + rollback_error) from restore_error
    try:
        shutil.rmtree(staging)
    except OSError as cleanup_error:
        print(
            "WARNING: 移轉成功，但舊資料暫存目錄無法自動清除，請限制存取並人工檢查："
            + str(staging)
            + " ("
            + str(cleanup_error)
            + ")",
            file=sys.stderr,
        )
    return info, backup


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect or restore dorm staff portable data")
    parser.add_argument("command", choices=("inspect", "restore"))
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = args.project_root.expanduser().resolve()
    source = args.source.expanduser().resolve()
    try:
        if not (project_root / "wsgi.py").is_file() or not (project_root / "migrations").is_dir():
            raise MigrationError("目的資料夾不是有效的宿舍工讀生系統專案。")
        if args.command == "inspect":
            emit_info(inspect_source(source, project_root))
        else:
            info, backup = restore_source(source, project_root)
            emit_info(info)
            print("MIGRATION_PREVIOUS_BACKUP=" + (str(backup) if backup else "NONE"))
            print("MIGRATION_RESULT=SUCCESS")
        return 0
    except MigrationError as exc:
        print("MIGRATION_RESULT=FAILED", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
