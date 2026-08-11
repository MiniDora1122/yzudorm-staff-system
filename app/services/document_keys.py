from __future__ import annotations

import os
import base64
import hashlib
from pathlib import Path

from cryptography.fernet import Fernet


KEY_FILENAME = "document-fernet.key"


def _validate_key(value: bytes) -> bytes:
    value = value.strip()
    try:
        Fernet(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("文件加密金鑰格式錯誤，為避免文件無法解密，系統已停止啟動。") from exc
    return value


def _read_key(path: Path) -> tuple[bytes | None, bool]:
    if not path.exists():
        return None, False
    try:
        return _validate_key(path.read_bytes()), False
    except RuntimeError:
        return None, True


def _atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(value + b"\n")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def ensure_document_encryption_key(app) -> dict:
    """Load or generate a stable key and maintain a second local backup copy."""
    primary = Path(app.config["DOCUMENT_KEY_DIR"]).resolve() / KEY_FILENAME
    backup = Path(app.config["DOCUMENT_KEY_BACKUP_DIR"]).resolve() / KEY_FILENAME
    configured = app.config.get("DOCUMENT_ENCRYPTION_KEY")
    configured_key = _validate_key(configured.encode("ascii")) if configured else None
    primary_key, primary_invalid = _read_key(primary)
    backup_key, backup_invalid = _read_key(backup)

    existing = primary_key or backup_key
    if primary_key and backup_key and primary_key != backup_key:
        raise RuntimeError("文件主金鑰與備份金鑰不一致，為避免資料損毀，系統已停止啟動。")
    if configured_key and existing and configured_key != existing:
        raise RuntimeError("環境變數金鑰與既有文件金鑰不一致；請還原原金鑰後再啟動。")
    if configured_key is None and existing is None and (primary_invalid or backup_invalid):
        raise RuntimeError("文件主金鑰與備份皆無法使用，為避免產生新金鑰覆蓋舊資料，系統已停止啟動。")

    legacy_files = list(Path(app.config["DOCUMENT_STORAGE_DIR"]).glob("**/*.bin"))
    legacy_key = None
    if existing is None and configured_key is None and legacy_files:
        secret = str(app.config["SECRET_KEY"]).encode("utf-8")
        legacy_key = base64.urlsafe_b64encode(hashlib.sha256(b"staff-document:" + secret).digest())
    key = configured_key or existing or legacy_key or Fernet.generate_key()
    if primary_key is None:
        _atomic_write(primary, key)
    if backup_key is None:
        _atomic_write(backup, key)
    app.config["DOCUMENT_ENCRYPTION_KEY"] = key.decode("ascii")
    app.config["DOCUMENT_KEY_PRIMARY_PATH"] = str(primary)
    app.config["DOCUMENT_KEY_BACKUP_PATH"] = str(backup)
    return {
        "primary": primary,
        "backup": backup,
        "generated": existing is None and configured_key is None and legacy_key is None,
        "legacy_migrated": legacy_key is not None,
    }
