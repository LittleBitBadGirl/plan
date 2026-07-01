"""
Конфигурация pytest для E2E тестов планировщика задач.
Использует тестовую БД в памяти и реальный FastAPI app через AsyncClient.
"""
import os
import sys
import tempfile
import pytest
import pytest_asyncio
from pathlib import Path
from httpx import AsyncClient, ASGITransport
from sqlalchemy import delete

# Добавляем корень проекта в PYTHONPATH
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Тестовая БД и API-токен ДО импорта app
TEST_DB_PATH = tempfile.mktemp(suffix=".db")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{TEST_DB_PATH}")
os.environ.setdefault("API_TOKEN", "test-api-token")

from app.main import app
from app.db.database import async_session, init_db, engine
from app.models.base import Base
from app.models.task import Task
from app.models.calendar_event import CalendarEvent
from app.models.recurring import RecurringTask
from app.models.recurring_completion import RecurringCompletion
from app.models.shopping import ShoppingItem

TEST_AUTH_HEADERS = {
    "Authorization": "Bearer test-api-token",
}


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_db():
    """Создать таблицы в тестовой БД и засидить категории"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from app.db.seed import seed_categories

    async with async_session() as session:
        await seed_categories(session)
        await session.commit()

    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(autouse=True)
async def isolate_tasks_and_calendar():
    """Чистые tasks/calendar/recurring между тестами."""
    async with async_session() as session:
        await session.execute(delete(RecurringCompletion))
        await session.execute(delete(CalendarEvent))
        await session.execute(delete(ShoppingItem))
        await session.execute(delete(Task))
        await session.execute(delete(RecurringTask))
        await session.commit()
    yield


@pytest_asyncio.fixture(scope="function")
async def client():
    """Асинхронный HTTP клиент для FastAPI с тестовым токеном"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=TEST_AUTH_HEADERS,
    ) as ac:
        yield ac


@pytest_asyncio.fixture(scope="function")
async def db():
    """Сессия БД для прямой работы с моделями"""
    async with async_session() as session:
        yield session
        await session.rollback()
