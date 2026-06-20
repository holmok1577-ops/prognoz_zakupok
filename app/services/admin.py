from __future__ import annotations

import gzip
import hashlib
import hmac
import os
import secrets
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AdminAuditLog, AdminSession, AdminUser


settings = get_settings()
SESSION_COOKIE = "flower_admin_session"
HASH_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, HASH_ITERATIONS)
    return f"pbkdf2_sha256${HASH_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def ensure_default_admin(db: Session) -> None:
    if not settings.admin_auth_enabled:
        return
    existing = db.scalar(select(AdminUser).limit(1))
    if existing:
        return
    initial_password = settings.admin_initial_password.strip()
    if not initial_password:
        return
    db.add(AdminUser(username=settings.admin_username, password_hash=hash_password(initial_password)))
    db.commit()


def authenticate_admin(db: Session, username: str, password: str) -> AdminUser | None:
    ensure_default_admin(db)
    user = db.scalar(select(AdminUser).where(AdminUser.username == username.strip()))
    if user and verify_password(password, user.password_hash):
        return user
    return None


def create_session(db: Session, user: AdminUser) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = _token_hash(token)
    expires_at = datetime.utcnow() + timedelta(days=settings.admin_session_days)
    db.add(AdminSession(token_hash=token_hash, user_id=user.id, expires_at=expires_at))
    db.commit()
    return token


def get_user_by_session_token(db: Session, token: str | None) -> AdminUser | None:
    if not token:
        return None
    session = db.scalar(
        select(AdminSession)
        .where(AdminSession.token_hash == _token_hash(token), AdminSession.expires_at > datetime.utcnow())
        .limit(1)
    )
    return session.user if session else None


def delete_session(db: Session, token: str | None) -> None:
    if not token:
        return
    session = db.scalar(select(AdminSession).where(AdminSession.token_hash == _token_hash(token)).limit(1))
    if session:
        db.delete(session)
        db.commit()


def change_password(
    db: Session,
    user: AdminUser,
    current_password: str,
    new_password: str,
    repeated_password: str,
) -> tuple[bool, str]:
    if not verify_password(current_password, user.password_hash):
        return False, "Текущий пароль указан неверно."
    if len(new_password) < 8:
        return False, "Новый пароль должен быть не короче 8 символов."
    if new_password != repeated_password:
        return False, "Новый пароль и повтор пароля не совпадают."
    user.password_hash = hash_password(new_password)
    user.updated_at = datetime.utcnow()
    db.query(AdminSession).filter(AdminSession.user_id == user.id).delete()
    db.add(AdminAuditLog(username=user.username, action="change_password", details="Пароль администратора изменен."))
    db.commit()
    return True, "Пароль изменен. Войдите заново."


def list_backups() -> list[dict]:
    backup_dir = _backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sorted(backup_dir.glob("*.db.gz"), key=lambda item: item.stat().st_mtime, reverse=True):
        stat = path.stat()
        rows.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime),
            }
        )
    return rows


def create_backup(username: str = "") -> Path:
    backup_dir = _backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    source = _sqlite_db_path()
    if not source.exists():
        raise FileNotFoundError(f"База данных не найдена: {source}")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    temp_path = backup_dir / f"manual-{timestamp}.db"
    gz_path = backup_dir / f"manual-{timestamp}.db.gz"
    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(temp_path)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()
    with temp_path.open("rb") as src, gzip.open(gz_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    temp_path.unlink(missing_ok=True)
    return gz_path


def restore_backup(db: Session, username: str, backup_name: str) -> None:
    backup_dir = _backup_dir().resolve()
    backup_path = (backup_dir / backup_name).resolve()
    if backup_dir not in backup_path.parents or not backup_path.name.endswith(".db.gz"):
        raise ValueError("Некорректный файл backup.")
    if not backup_path.exists():
        raise FileNotFoundError("Backup не найден.")
    restore_copy = create_backup(username=username)
    db.add(
        AdminAuditLog(
            username=username,
            action="restore_backup_started",
            details=f"Перед восстановлением создан backup: {restore_copy.name}. Восстановление из: {backup_path.name}",
        )
    )
    db.commit()
    db.close()
    from app.db import engine

    engine.dispose()
    target = _sqlite_db_path()
    temp_target = target.with_suffix(".restore.tmp")
    with gzip.open(backup_path, "rb") as src, temp_target.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    os.replace(temp_target, target)


def audit_log(db: Session, limit: int = 30) -> list[AdminAuditLog]:
    return list(db.scalars(select(AdminAuditLog).order_by(desc(AdminAuditLog.created_at)).limit(limit)))


def log_action(db: Session, username: str, action: str, details: str = "") -> None:
    db.add(AdminAuditLog(username=username, action=action, details=details))
    db.commit()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _backup_dir() -> Path:
    return Path(settings.admin_backup_dir)


def _sqlite_db_path() -> Path:
    database_url = settings.database_url
    if not database_url.startswith("sqlite:///"):
        raise RuntimeError("Backup/restore сейчас поддерживает только SQLite.")
    raw_path = database_url.replace("sqlite:///", "", 1)
    return Path(raw_path)
