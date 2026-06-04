import asyncio
import os
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import unquote

from app.config import settings
from app.utils.logger import app_logger

BACKUP_DIR = "backups"


def _database_file_path() -> str:
    """Путь к файлу SQLite из DATABASE_URL."""
    prefix = "sqlite+aiosqlite:///"
    url = settings.database_url
    if url.startswith(prefix):
        return unquote(url[len(prefix) :])
    return str(settings.project_dir / "planner.db")


def backup_sqlite_file(src_path: str, dst_path: str) -> None:
    """Консистентный снимок БД (безопасно при WAL, в отличие от shutil.copy)."""
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dst_path)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


async def create_backup():
    """Создать резервную копию базы данных"""
    db_path = _database_file_path()
    if not os.path.exists(db_path):
        app_logger.warning("⚠️ Файл базы данных не найден для бэкапа")
        return

    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    backup_path = os.path.join(BACKUP_DIR, f"planner_{timestamp}.db")

    try:
        await asyncio.to_thread(backup_sqlite_file, db_path, backup_path)
        app_logger.info(f"✅ Бэкап создан: {backup_path}")
        await rotate_backups()
    except Exception as e:
        app_logger.error(f"❌ Ошибка при создании бэкапа: {e}")


async def rotate_backups():
    """Удалить бэкапы старше 7 дней"""
    if not os.path.isdir(BACKUP_DIR):
        return

    now = datetime.now()
    retention_days = 7

    for filename in os.listdir(BACKUP_DIR):
        if not filename.startswith("planner_") or not filename.endswith(".db"):
            continue

        file_path = os.path.join(BACKUP_DIR, filename)
        file_time = datetime.fromtimestamp(os.path.getmtime(file_path))

        if now - file_time > timedelta(days=retention_days):
            try:
                os.remove(file_path)
                app_logger.info(f"🗑 Удален старый бэкап: {filename}")
            except Exception as e:
                app_logger.error(f"❌ Ошибка при удалении бэкапа {filename}: {e}")
