from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from pathlib import Path
import os
import asyncio

from aiogram import Bot, Dispatcher
from app.bot.handlers import router as bot_router
from app.bot.sync import sync_missed_messages
from app.db.database import init_db, async_session
from app.db.seed import seed_categories
from app.api.tasks import router as tasks_router
from app.api.categories import router as categories_router
from app.web.pages import router as web_router
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan: инициализация БД и seed категорий"""
    # Startup
    await init_db()

    # Создание таблиц
    from app.models.base import Base
    from app.db.database import engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed категорий, если БД пуста
    async with async_session() as db:
        await seed_categories(db)
        await db.commit()

    # Запуск Telegram бота
    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    dp.include_router(bot_router)

    # Синхронизация пропущенных
    await sync_missed_messages(bot)

    # Запуск polling в фоне
    asyncio.create_task(dp.start_polling(bot))

    yield

    # Shutdown
    await bot.session.close()


app = FastAPI(
    title="Task Planner",
    description="Персональный планировщик задач",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session middleware (для аутентификации)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("API_TOKEN", "fallback-secret-key-change-in-production"),
    session_cookie="planner_session",
    max_age=7 * 24 * 60 * 60,  # 7 дней
    https_only=False,
    same_site="lax",
)

# Статические файлы (веб-интерфейс)
static_dir = Path(__file__).parent / "web" / "static"
app.mount("/web/static", StaticFiles(directory=str(static_dir)), name="web_static")

# Статические файлы (загрузки)
uploads_dir = Path(__file__).parent.parent / "uploads"
uploads_dir.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")

# Роуты
app.include_router(tasks_router)
app.include_router(categories_router)
app.include_router(web_router)


@app.get("/api/health")
async def health():
    """Проверка здоровья"""
    return {"status": "ok", "version": "0.1.0"}
