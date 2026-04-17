from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pathlib import Path

from app.utils.logger import app_logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.db.database import init_db, async_session
from app.db.seed import seed_categories
from app.api.tasks import router as tasks_router
from app.api.categories import router as categories_router
from app.api.recurring import router as recurring_router
from app.api.ai import router as ai_router
from app.api.screenshot import router as screenshot_router
from app.web.pages import router as web_router
from app.config import settings
from app.services.rollover_service import rollover_overdue_tasks
from app.services.recurring_service import generate_recurring_tasks


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan: инициализация БД и seed категорий"""
    app_logger.info("🚀 Запуск планировщика задач...")
    
    # Startup
    await init_db()

    # Создание таблиц
    from app.models.base import Base
    from app.db.database import engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Добавление completed_count и UNIQUE constraint если нет
    import sqlite3
    db_path = settings.project_dir / "planner.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("ALTER TABLE recurring_tasks ADD COLUMN completed_count INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass  # Колонка уже есть

    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_recurring_unique ON recurring_tasks(title, recurrence_type)")
        conn.commit()
    except Exception:
        pass

    # Создаём таблицу для хранения дат выполнения периодических задач
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recurring_completions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recurring_task_id INTEGER NOT NULL,
                completed_date DATE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (recurring_task_id) REFERENCES recurring_tasks(id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recurring_completions_date ON recurring_completions(completed_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recurring_completions_task ON recurring_completions(recurring_task_id)")
        conn.commit()
    except Exception as e:
        app_logger.warning(f"Не удалось создать таблицу recurring_completions: {e}")
    finally:
        conn.close()

    # Seed категорий, если БД пуста
    async with async_session() as db:
        await seed_categories(db)
        await db.commit()

    # Перенос просроченных задач при запуске (если сервер не работал ночью)
    rollover_result = await rollover_overdue_tasks()
    if rollover_result["moved"] > 0:
        app_logger.info(f"🔄 Перенесено {rollover_result['moved']} просроченных задач (в т.ч. {rollover_result['new_chronic']} хронических)")

    # APScheduler — фоновые задачи
    scheduler = AsyncIOScheduler()

    # Генерация периодических задач ежедневно в 00:05
    scheduler.add_job(
        generate_recurring_tasks,
        CronTrigger(hour=0, minute=5),
        id="generate_recurring",
        name="Генерация периодческих задач",
    )

    # Перенос просроченных задач ежедневно в 00:10
    scheduler.add_job(
        rollover_overdue_tasks,
        CronTrigger(hour=0, minute=10),
        id="rollover_tasks",
        name="Перенос просроченных задач",
    )

    scheduler.start()
    app_logger.info("📅 APScheduler запущен")
    app_logger.info("✅ Планировщик запущен")

    yield

    # Shutdown
    app_logger.info("🛑 Остановка планировщика...")
    scheduler.shutdown()


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
app.include_router(recurring_router)
app.include_router(ai_router)
app.include_router(screenshot_router)
app.include_router(web_router)


@app.get("/api/health")
async def health():
    """Проверка здоровья"""
    return {"status": "ok", "version": "0.1.0"}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Глобальный обработчик ошибок"""
    app_logger.error(f"Ошибка: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Внутренняя ошибка сервера"},
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=404,
        content={"detail": "Не найдено"},
    )
