import sqlalchemy
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False}
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)



from app.models.base import Base
import sqlalchemy
from sqlalchemy import text

async def init_db():
    """Инициализация БД, миграции и WAL режим"""
    async with engine.begin() as conn:
        # 1. Создаем новые таблицы (transactions, financial_goals и т.д.)
        await conn.run_sync(Base.metadata.create_all)
        
        # 2. Настройки SQLite
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA busy_timeout=5000"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))

        # 3. Ручные миграции для существующих таблиц
        try:
            await conn.execute(text("ALTER TABLE categories ADD COLUMN type VARCHAR(20) DEFAULT 'task';"))
        except Exception: pass
            
        try:
            await conn.execute(text("ALTER TABLE tasks ADD COLUMN tags VARCHAR(500);"))
        except Exception: pass

        try:
            await conn.execute(text("ALTER TABLE tasks ADD COLUMN impact_notes TEXT;"))
        except Exception: pass

        try:
            await conn.execute(text("ALTER TABLE tasks ADD COLUMN is_milestone BOOLEAN DEFAULT 0;"))
        except Exception: pass

        try:
            await conn.execute(text("ALTER TABLE tasks ADD COLUMN estimated_minutes INTEGER;"))
        except Exception: pass

        try:
            await conn.execute(text("ALTER TABLE tasks ADD COLUMN actual_minutes INTEGER;"))
        except Exception: pass



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
