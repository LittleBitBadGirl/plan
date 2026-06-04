import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.migrate import run_migrations
from app.models.base import Base

# Импорты регистрируют таблицы в metadata
from app.models import calendar_event as _calendar_event_import  # noqa: F401
from app.models import calendar_ignore_rule as _calendar_ignore_rule_import  # noqa: F401
from app.models import period_entry as _period_entry_import  # noqa: F401
from app.models import recurring_completion as _recurring_completion_import  # noqa: F401

engine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False},
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db():
    """Создание таблиц, PRAGMA SQLite, Alembic до head."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA busy_timeout=5000"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))

    await asyncio.to_thread(run_migrations)


async def get_db():
    """Dependency для получения сессии БД"""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
