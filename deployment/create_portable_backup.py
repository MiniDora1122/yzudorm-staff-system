"""Create a self-contained, sensitive backup for moving the application."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "instance",
    "outputs",
    "tmp",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a portable encrypted-data backup")
    parser.add_argument("destination", type=Path, help="Output ZIP path")
    parser.add_argument(
        "--allow-running",
        action="store_true",
        help="Run while 127.0.0.1:8000 is listening (documents may change)",
    )
    return parser.parse_args()


def service_is_running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 8000), timeout=0.25):
            return True
    except OSError:
        return False


def resolve_path(value: str | None, default: Path) -> Path:
    if not value:
        return default.resolve()
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


def sqlite_path(database_url: str | None) -> Path:
    if not database_url:
        return (PROJECT_ROOT / "instance" / "dorm_staff.db").resolve()
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise RuntimeError("Portable backup currently supports SQLite DATABASE_URL only.")
    raw = database_url[len(prefix) :]
    if raw == ":memory:":
        raise RuntimeError("An in-memory SQLite database cannot be backed up.")
    # sqlite:////C:/... 或 sqlite:///C:/... 在 Windows 都可能出現。
    raw = raw.lstrip("/") if len(raw) > 2 and raw[1:3] == ":/" else raw
    candidate = Path(raw)
    if not candidate.is_absolute():
        # Flask-SQLAlchemy resolves a relative SQLite path from Flask's
        # instance directory, not from the process working directory.
        candidate = PROJECT_ROOT / "instance" / candidate
    return candidate.resolve()


def portable_env(source: dict[str, str | None]) -> str:
    values = {key: value for key, value in source.items() if value is not None}
    values.update(
        {
            "DATABASE_URL": "sqlite:///dorm_staff.db",
            "DOCUMENT_STORAGE_DIR": "instance/private_documents",
            "DOCUMENT_KEY_DIR": "instance/private_keys",
            "DOCUMENT_KEY_BACKUP_DIR": "instance/private_keys/backup",
        }
    )
    lines = [f"{key}={value}" for key, value in sorted(values.items())]
    return "\n".join(lines) + "\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def add_file(
    archive: zipfile.ZipFile,
    source: Path,
    archive_name: str,
    manifest_files: dict[str, dict[str, object]],
) -> None:
    data = source.read_bytes()
    archive.writestr(archive_name, data)
    manifest_files[archive_name] = {"size": len(data), "sha256": sha256_bytes(data)}


def iter_project_files(destination: Path):
    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file() or path.resolve() == destination:
            continue
        relative = path.relative_to(PROJECT_ROOT)
        if any(part in EXCLUDED_PARTS or part.startswith(".venv-broken-") for part in relative.parts):
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES or relative.as_posix() == ".env":
            continue
        yield path, relative.as_posix()


def add_tree(
    archive: zipfile.ZipFile,
    source_root: Path,
    archive_root: str,
    manifest_files: dict[str, dict[str, object]],
) -> None:
    if not source_root.exists():
        return
    for path in source_root.rglob("*"):
        if path.is_file():
            relative = path.relative_to(source_root).as_posix()
            add_file(archive, path, f"{archive_root}/{relative}", manifest_files)


def main() -> int:
    args = parse_args()
    destination = args.destination.expanduser().resolve()
    if service_is_running() and not args.allow_running:
        raise RuntimeError(
            "Waitress is running on 127.0.0.1:8000. Stop the scheduled service, "
            "or use --allow-running only when no document upload is in progress."
        )

    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        raise RuntimeError(".env not found; data and key locations cannot be resolved.")
    env = dotenv_values(env_path)
    db_path = sqlite_path(env.get("DATABASE_URL"))
    document_dir = resolve_path(
        env.get("DOCUMENT_STORAGE_DIR"), PROJECT_ROOT / "instance" / "private_documents"
    )
    key_dir = resolve_path(
        env.get("DOCUMENT_KEY_DIR"), PROJECT_ROOT / "instance" / "private_keys"
    )
    if not db_path.is_file():
        raise RuntimeError(f"SQLite database not found: {db_path}")
    if not key_dir.is_dir():
        raise RuntimeError(f"Document key directory not found: {key_dir}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_files: dict[str, dict[str, object]] = {}
    created_at = datetime.now(timezone.utc).isoformat()

    with tempfile.TemporaryDirectory(prefix="dorm-staff-backup-") as temp_dir:
        db_snapshot = Path(temp_dir) / "dorm_staff.db"
        source_db = sqlite3.connect(db_path)
        target_db = sqlite3.connect(db_snapshot)
        try:
            source_db.backup(target_db)
        finally:
            target_db.close()
            source_db.close()

        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source, name in iter_project_files(destination):
                add_file(archive, source, name, manifest_files)
            add_file(archive, db_snapshot, "instance/dorm_staff.db", manifest_files)
            add_tree(archive, document_dir, "instance/private_documents", manifest_files)
            add_tree(archive, key_dir, "instance/private_keys", manifest_files)

            env_data = portable_env(env).encode("utf-8")
            archive.writestr(".env", env_data)
            manifest_files[".env"] = {"size": len(env_data), "sha256": sha256_bytes(env_data)}
            manifest = {
                "format": "dorm-staff-portable-backup-v1",
                "created_at_utc": created_at,
                "file_count": len(manifest_files),
                "files": manifest_files,
            }
            archive.writestr(
                "PORTABLE_BACKUP_MANIFEST.json",
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )

    print("Backup created successfully.")
    print(
        "WARNING: this ZIP contains the database, personal data, document keys, "
        "and SECRET_KEY. Store it on encrypted media with restricted access."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
