import os
import shutil
from datetime import datetime, timedelta
from app.utils.logger import app_logger

DB_PATH = "planner.db"
BACKUP_DIR = "backups"

async def create_backup():
    """Создать резервную копию базы данных"""
    if not os.path.exists(DB_PATH):
        app_logger.warning("⚠️ Файл базы данных не найден для бэкапа")
        return

    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)

    # Имя файла: planner_2026-04-23_14-30.db
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    backup_path = os.path.join(BACKUP_DIR, f"planner_{timestamp}.db")

    try:
        shutil.copy2(DB_PATH, backup_path)
        app_logger.info(f"✅ Бэкап создан: {backup_path}")
        await rotate_backups()
    except Exception as e:
        app_logger.error(f"❌ Ошибка при создании бэкапа: {e}")

async def rotate_backups():
    """Удалить бэкапы старше 7 дней"""
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
