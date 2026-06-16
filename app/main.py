import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pathlib import Path

from app.utils.logger import app_logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from app.db.database import init_db, async_session
from app.db.seed import seed_categories
from app.api.tasks import router as tasks_router
from app.api.categories import router as categories_router
from app.api.recurring import router as recurring_router
from app.api.habits import router as habits_router
from app.api.period import router as period_router
from app.api.ai import router as ai_router
from app.api.screenshot import router as screenshot_router
from app.web.pages import router as web_router
from app.web.auth_routes import router as auth_router
from app.config import settings
from app.middleware import ApiAuthMiddleware
from app.services.rollover_service import rollover_overdue_tasks
from app.services.recurring_service import generate_recurring_tasks
from app.services.backup_service import create_backup
from app.services.calendar_sync_service import sync_calendar_events


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan: инициализация БД и seed категорий"""
    app_logger.info("🚀 Запуск планировщика задач...")
    
    # Startup
    await init_db()

    # Seed категорий, если БД пуста
    async with async_session() as db:
        await seed_categories(db)
        await db.commit()

    # Сделать бэкап при запуске
    await create_backup()

    # Перенос просроченных задач при запуске
    rollover_result = await rollover_overdue_tasks()

    # APScheduler — фоновые задачи
    scheduler = AsyncIOScheduler()

    # Бэкап БД ежедневно в 00:01
    scheduler.add_job(
        create_backup,
        CronTrigger(hour=0, minute=1),
        id="backup_db",
        name="Резервное копирование БД",
    )

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

    calendar_active = settings.calendar_sync_enabled or (
        settings.google_calendar_sync_enabled and settings.google_calendar_ical_url
    )
    if calendar_active:
        scheduler.add_job(
            sync_calendar_events,
            IntervalTrigger(minutes=30),
            id="calendar_sync",
            name="Синхронизация календарей",
        )

    scheduler.start()
    app_logger.info("📅 APScheduler запущен")
    app_logger.info("✅ Планировщик запущен")

    async def _startup_calendar_sync() -> None:
        if not calendar_active:
            return
        try:
            await asyncio.wait_for(sync_calendar_events(), timeout=45.0)
        except Exception as e:
            app_logger.warning(f"Calendar sync on startup skipped: {e}")

    asyncio.create_task(_startup_calendar_sync())

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

app.add_middleware(ApiAuthMiddleware)

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
app.include_router(habits_router)
app.include_router(period_router)
app.include_router(ai_router)
app.include_router(screenshot_router)
app.include_router(auth_router)
app.include_router(web_router)


@app.get("/api/health")
async def health():
    """Проверка здоровья"""
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/ping")
async def ping():
    """Проверка: какая версия кода задеплоена."""
    import subprocess, os
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=os.path.dirname(__file__) + "/.."
        ).stdout.strip()
    except Exception:
        sha = "unknown"
    return {"status": "ok", "commit": sha, "note": "debug handler active"}


@app.post("/api/admin/fix-transfers")
async def fix_transfers(request: Request):
    """ВРЕМЕННЫЙ: правка персональных переводов."""
    from app.db.database import async_session
    from app.models.finance import Transaction
    from app.models.category import Category
    from sqlalchemy import select, delete

    async with async_session() as db:
        all_cats = (await db.execute(select(Category))).scalars().all()
        cat_by_name = {c.name: c.id for c in all_cats}

        result = {"deleted": [], "recategorized": [], "uncategorized": []}

        # === DELETE duplicates and internal transfers ===
        to_delete = [
            ("Ольга П.", 32500),         # дубль шашлыков
            ("перевод между своими", None),  # все внутренние переводы
        ]
        # Delete ONE Роман И. -4450 duplicate (keep the first)
        roman_dups = (await db.execute(
            select(Transaction).where(
                Transaction.description == "Роман И.",
                Transaction.amount == -4450
            ).order_by(Transaction.id)
        )).scalars().all()
        if len(roman_dups) > 1:
            await db.delete(roman_dups[1])
            result["deleted"].append(f"Роман И. дубль -4450 (id={roman_dups[1].id})")

        # Delete Ольга П. duplicate (keep first)
        olga_dups = (await db.execute(
            select(Transaction).where(
                Transaction.description == "Ольга П.",
                Transaction.amount == 32500
            ).order_by(Transaction.id)
        )).scalars().all()
        if len(olga_dups) > 1:
            await db.delete(olga_dups[1])
            result["deleted"].append(f"Ольга П. дубль 32500 (id={olga_dups[1].id})")

        # Delete все "перевод между своими счетами"
        internal = (await db.execute(
            select(Transaction).where(
                Transaction.description.like("%своими счетами%")
            )
        )).scalars().all()
        for tx in internal:
            result["deleted"].append(f"Внутренний перевод {tx.amount} (id={tx.id})")
            await db.delete(tx)

        await db.flush()

        # === RECATEGORIZE ===
        recats = [
            # (description_pattern, amount, new_category)
            ("Роман И.", 1200, "Возврат"),
            ("Роман И.", -4450, "Такси"),
            ("Перевод себе в другой банк", -30000, "Ипотека"),
            ("Полина Ш.", None, None),  # special: positive→Возврат, negative→NULL
        ]

        # Роман И.
        for desc, amt, new_cat in recats:
            if desc == "Полина Ш.":
                continue
            query = select(Transaction).where(Transaction.description.like(f"%{desc}%"))
            if amt is not None:
                query = query.where(Transaction.amount == amt)
            txs = (await db.execute(query)).scalars().all()
            for tx in txs:
                old_cat = tx.category_id
                tx.category_id = cat_by_name.get(new_cat)
                result["recategorized"].append(f"{desc} {tx.amount} → {new_cat}")

        # Полина Ш. special
        polina_txs = (await db.execute(
            select(Transaction).where(Transaction.description.like("%Полина Ш%"))
        )).scalars().all()
        for tx in polina_txs:
            if tx.amount and tx.amount > 0:
                tx.category_id = cat_by_name.get("Возврат")
                result["recategorized"].append(f"Полина Ш. {tx.amount} → Возврат")
            else:
                tx.category_id = None
                result["uncategorized"].append(f"Полина Ш. {tx.amount} → без категории")

        # Наталия Б. -500 → Подарки
        natalia = (await db.execute(
            select(Transaction).where(Transaction.description.like("%Наталия%"), Transaction.amount == -500)
        )).scalars().all()
        for tx in natalia:
            tx.category_id = cat_by_name.get("Подарки")
            result["recategorized"].append(f"Наталия Б. → Подарки")

        # Георгий М. (все) → Хозтовары
        georgiy = (await db.execute(
            select(Transaction).where(Transaction.description.like("%Георгий%"))
        )).scalars().all()
        for tx in georgiy:
            tx.category_id = cat_by_name.get("Хозтовары")
            result["recategorized"].append(f"Георгий М. {tx.amount} → Хозтовары")

        # FURSOV/Fursor → Прочее
        fursov = (await db.execute(
            select(Transaction).where(
                Transaction.description.like("%FURS%") | Transaction.description.like("%Furs%")
            )
        )).scalars().all()
        for tx in fursov:
            tx.category_id = cat_by_name.get("Прочее")
            result["recategorized"].append(f"FURSOV {tx.amount} → Прочее")

        # KUCHMINA → Прочее
        kuch = (await db.execute(
            select(Transaction).where(Transaction.description.like("%KUCHMINA%"))
        )).scalars().all()
        for tx in kuch:
            tx.category_id = cat_by_name.get("Прочее")
            result["recategorized"].append(f"KUCHMINA {tx.amount} → Прочее")

        await db.commit()

    return JSONResponse(result)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Глобальный обработчик ошибок — пишет в файл для отладки."""
    import traceback, os
    tb = traceback.format_exc()
    msg = f"{type(exc).__name__}: {exc}"
    app_logger.error(f"Ошибка: {exc}", exc_info=True)
    
    # Write to debug file
    try:
        debug_dir = os.path.join(os.path.dirname(__file__), "..", "uploads")
        os.makedirs(debug_dir, exist_ok=True)
        with open(os.path.join(debug_dir, "last_error.txt"), "w") as f:
            f.write(f"URL: {request.url}\n")
            f.write(f"Method: {request.method}\n")
            f.write(f"Error: {msg}\n")
            f.write(f"Traceback:\n{tb}\n")
    except Exception:
        pass
    
    return JSONResponse(
        status_code=500,
        content={
            "detail": msg,
            "traceback": tb,
            "debug_file": "/uploads/last_error.txt",
        },
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=404,
        content={"detail": "Не найдено"},
    )
