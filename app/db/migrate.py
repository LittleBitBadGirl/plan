"""Запуск Alembic из приложения (sync SQLite)."""

from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def sync_database_url() -> str:
    """URL для Alembic (без aiosqlite)."""
    prefix = "sqlite+aiosqlite:///"
    url = settings.database_url
    if url.startswith(prefix):
        return f"sqlite:///{url[len(prefix):]}"
    return url.replace("+aiosqlite", "")


def run_migrations() -> None:
    """Применить миграции до head (вызывать после create_all)."""
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", sync_database_url())
    command.upgrade(cfg, "head")
