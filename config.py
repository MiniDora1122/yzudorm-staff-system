import os
from datetime import timedelta
from pathlib import Path

from cachelib.file import FileSystemCache
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", f"sqlite:///{(BASE_DIR / 'instance' / 'dorm_staff.db').as_posix()}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    PERMANENT_SESSION_LIFETIME = timedelta(
        minutes=int(os.getenv("SESSION_TIMEOUT_MINUTES", "30"))
    )
    SESSION_TYPE = os.getenv("SESSION_TYPE", "cachelib")
    SESSION_CACHELIB = FileSystemCache(
        cache_dir=str(BASE_DIR / "instance" / "sessions"), threshold=500
    )
    SESSION_PERMANENT = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = env_flag("SESSION_COOKIE_SECURE", False)
    TRUST_PROXY = env_flag("TRUST_PROXY", False)
    PROXY_FIX_X_FOR = int(os.getenv("PROXY_FIX_X_FOR", "1"))
    PROXY_FIX_X_PROTO = int(os.getenv("PROXY_FIX_X_PROTO", "1"))
    PROXY_FIX_X_HOST = int(os.getenv("PROXY_FIX_X_HOST", "1"))
    WTF_CSRF_TIME_LIMIT = None
    APP_TIMEZONE = "Asia/Taipei"
    MAX_DOCUMENT_FILE_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(8 * 1024 * 1024)))
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_REQUEST_UPLOAD_BYTES", str(20 * 1024 * 1024)))
    DOCUMENT_STORAGE_DIR = os.getenv(
        "DOCUMENT_STORAGE_DIR", str(BASE_DIR / "instance" / "private_documents")
    )
    DOCUMENT_KEY_DIR = os.getenv(
        "DOCUMENT_KEY_DIR", str(BASE_DIR / "instance" / "private_keys")
    )
    DOCUMENT_KEY_BACKUP_DIR = os.getenv(
        "DOCUMENT_KEY_BACKUP_DIR", str(BASE_DIR / "instance" / "private_keys" / "backup")
    )
    DOCUMENT_MAX_PIXELS = int(os.getenv("DOCUMENT_MAX_PIXELS", "20000000"))
    DOCUMENT_DEFAULT_RETENTION_DAYS = int(os.getenv("DOCUMENT_DEFAULT_RETENTION_DAYS", "365"))
    DOCUMENT_CLEANUP_SCHEDULER_ENABLED = env_flag("DOCUMENT_CLEANUP_SCHEDULER_ENABLED", True)
    DOCUMENT_CLEANUP_CHECK_SECONDS = int(os.getenv("DOCUMENT_CLEANUP_CHECK_SECONDS", "300"))
    MAINTENANCE_SCHEDULER_ENABLED = env_flag("MAINTENANCE_SCHEDULER_ENABLED", True)
    MAINTENANCE_CHECK_SECONDS = int(os.getenv("MAINTENANCE_CHECK_SECONDS", "300"))
    AUTOMATIC_BACKUP_ENABLED = env_flag("AUTOMATIC_BACKUP_ENABLED", True)
    AUTOMATIC_BACKUP_HOUR = int(os.getenv("AUTOMATIC_BACKUP_HOUR", "2"))
    AUTOMATIC_BACKUP_MINUTE = int(os.getenv("AUTOMATIC_BACKUP_MINUTE", "0"))
    AUTOMATIC_BACKUP_RETENTION_DAYS = int(os.getenv("AUTOMATIC_BACKUP_RETENTION_DAYS", "30"))
    AUTOMATIC_BACKUP_DIR = os.getenv(
        "AUTOMATIC_BACKUP_DIR", str(BASE_DIR / "instance" / "automatic_backups")
    )
    DOCUMENT_ENCRYPTION_KEY = os.getenv("DOCUMENT_ENCRYPTION_KEY")
    EXPIRY_WARNING_DAYS = (60, 30)
    NOTIFICATION_SYNC_INTERVAL_SECONDS = int(os.getenv("NOTIFICATION_SYNC_INTERVAL_SECONDS", "120"))


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    TRUST_PROXY = False
    MAINTENANCE_SCHEDULER_ENABLED = False
    AUTOMATIC_BACKUP_ENABLED = False
