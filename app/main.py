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


@app.post("/api/admin/fix-finances")
async def fix_finances(request: Request):
    """ВРЕМЕННЫЙ: миграция финансов — категории, знаки, май."""
    from app.db.database import async_session
    from app.models.category import Category
    from app.models.finance import Transaction
    from sqlalchemy import select, func

    steps = []

    async with async_session() as db:
        # === Шаг 1: Создать недостающие родительские категории ===
        new_parents = {
            "Финансы": None,
            "Связь": None,
            "Красота": None,
            "Доходы": None,
        }
        created_parents = {}
        for name, parent in new_parents.items():
            existing = (await db.execute(select(Category).where(Category.name == name))).scalar()
            if not existing:
                cat = Category(name=name, parent_id=None)
                db.add(cat)
                await db.flush()
                created_parents[name] = cat.id
            else:
                created_parents[name] = existing.id

        # Rename Отдых → Развлечения
        otdyh = (await db.execute(select(Category).where(Category.name == "Отдых"))).scalar()
        if otdyh:
            otdyh.name = "Развлечения"

        # === Шаг 2: Поправить иерархию ===
        hierarchy_fixes = {
            "Табак": "Еда",
            "ИИС": "Финансы",
            "Подушка": "Финансы",
            "Подписки": "Связь",
            "Салоны": "Красота",
            "Косметолог": "Красота",
            "Хозтовары": "Дом",
            "Метро": "Еда",
            "Аптеки": "Здоровье",
            "Бензин": "Транспорт",
            "Парковка": "Транспорт",
            "Такси": "Транспорт",
            "Ипотека": "Дом",
            "Коммуналка": "Дом",
            "Налоги": "Дом",
            "Продукты": "Еда",
            "Рестораны": "Еда",
            "Фастфуд": "Еда",
            "Стирка": "Вертикаль",
        }

        fixed_hierarchy = 0
        for child_name, parent_name in hierarchy_fixes.items():
            child = (await db.execute(select(Category).where(Category.name == child_name))).scalar()
            parent = (await db.execute(select(Category).where(Category.name == parent_name))).scalar()
            if child and parent and child.parent_id != parent.id:
                child.parent_id = parent.id
                fixed_hierarchy += 1

        # Group income cats under Доходы
        income_cats = ["Зарплата", "Аванс", "Кэшбэк", "Возврат", "Авито", "Командировочные"]
        income_parent = created_parents.get("Доходы")
        income_fixed = 0
        if income_parent:
            for name in income_cats:
                cat = (await db.execute(select(Category).where(Category.name == name))).scalar()
                if cat and cat.parent_id != income_parent:
                    cat.parent_id = income_parent
                    income_fixed += 1

        # Also create missing subcategories
        missing_subs = {
            "Аренда": "Вертикаль",
            "Образование": "Даня",
            "Штрафы": "Дом",
            "Страховки": "Дом",
            "Одежда и обувь": "Вещи",
            "Книги": "Вещи",
            "Телефон": "Связь",
            "Брови": "Красота",
        }
        created_subs = 0
        for sub_name, parent_name in missing_subs.items():
            existing = (await db.execute(select(Category).where(Category.name == sub_name))).scalar()
            if not existing:
                parent = (await db.execute(select(Category).where(Category.name == parent_name))).scalar()
                if parent:
                    cat = Category(name=sub_name, parent_id=parent.id)
                    db.add(cat)
                    created_subs += 1

        await db.flush()
        steps.append(f"Категории: создано {len(created_parents)} родительских + {created_subs} дочерних, поправлено {fixed_hierarchy} иерархий, {income_fixed} доходных")

        # === Шаг 3: Инвертировать знаки (только если доходы ещё отрицательные) ===
        jan_check = (await db.execute(
            select(func.sum(Transaction.amount)).where(
                Transaction.date >= "2026-01-01",
                Transaction.date < "2026-02-01",
                Transaction.amount > 0
            )
        )).scalar() or 0

        if jan_check < 100000:  # January income should be ~258k
            all_tx = (await db.execute(select(Transaction))).scalars().all()
            for tx in all_tx:
                tx.amount = -tx.amount
            await db.flush()
            steps.append(f"Знаки: инвертировано {len(all_tx)} транзакций")
        else:
            steps.append(f"Знаки: уже корректны (январь доход={jan_check:,.0f}), пропущено")

        # === Шаг 4: Разобрать майские транзакции ===
        may_tx = (await db.execute(
            select(Transaction).where(
                Transaction.date >= "2026-05-01",
                Transaction.date < "2026-06-01",
                Transaction.category_id.is_(None)
            )
        )).scalars().all()

        # Build category lookup
        all_cats = (await db.execute(select(Category))).scalars().all()
        cat_by_name = {c.name: c.id for c in all_cats}

        # Classification rules: (keyword(s), category_name)
        rules = [
            # Еда
            (["шефмаркет", "лента", "магнит", "продукты", "пятёрочка", "окей", "ашан", "fix price",
              "лавка", "деливери", "3 сезона", "metro", "вкусвилл", "перекрёсток", "дикси",
              "ароматный мир", "красное&белое", "красное и белое", "росал"], "Продукты"),
            (["табак", "zhmud", "бристоль", "табако", "гэнг", "vapeshop", "кальян"], "Табак"),
            (["ресторан", "сыроварня", "cucumber", "кио кухня", "булки", "булочная",
              "mychara", "вкусно — и точка", "most coffee", "кофейня", "812", "rest",
              "u mari", "yamaguchi"], "Рестораны"),
            (["фастфуд", "булочные"], "Фастфуд"),
            (["метро"], "Метро"),
            # Дом
            (["ипотека", "сбер → ипотека"], "Ипотека"),
            (["коммуналка", "eirc", "epr"], "Коммуналка"),
            (["снт", "взнос"], "Коммуналка"),
            (["страховк", "sber life", "sberin"], "Коммуналка"),
            (["налоги", "фнс"], "Налоги"),
            (["озон", "wildberries"], "Вещи"),
            (["хозтовары"], "Хозтовары"),
            # Даня
            (["садик", "виктория д."], "Садик"),
            (["образование", "школа"], "Образование"),
            # Вертикаль
            (["стирка", "we-i-ramada", "ramada"], "Стирка"),
            (["аренда", "vertical-hotel"], "Аренда"),
            # Транспорт
            (["бензин", "татнефть", "газпромнефть", "азс", "заправка"], "Бензин"),
            (["такси", "яндекс go"], "Такси"),
            (["парковка"], "Парковка"),
            # Финансы
            (["иис", "совкомбанк"], "ИИС"),
            (["подушка", "бкс"], "Подушка"),
            # Связь
            (["телефон", "мегафон", "мтс", "билайн", "tele2"], "Телефон"),
            (["подписк", "subscription", "яндекс 360"], "Подписки"),
            # Здоровье
            (["аптека", "горздрав", "здоровье"], "Аптеки"),
            # Красота
            (["liks nail", "салон", "брови", "косметолог"], "Салоны"),
            # Развлечения
            (["evo_ekstrim", "sandiland", "термоланд"], "Развлечения"),
            # Подарки
            (["подарок", "цветы"], "Подарки"),
            # Благотворительность
            (["дари еду", "благотвор", "помощь рядом", "vmeste"], "Благотворительность"),
            # Доходы (положительные суммы)
            (["зарплата", "аванс", "зачисление"], "Зарплата"),
            (["возврат", "налоговый вычет"], "Возврат"),
            (["кэшбэк"], "Кэшбэк"),
            (["перевод"], "Возврат"),
        ]

        categorized = 0
        extra_income = 0
        for tx in may_tx:
            desc = (tx.description or "").lower()
            # Special: positive amounts with person names → income
            if tx.amount and tx.amount > 0:
                extra_income += 1
                tx.category_id = cat_by_name.get("Возврат") or cat_by_name.get("Прочее")
                categorized += 1
                continue

            for keywords, cat_name in rules:
                if any(kw in desc for kw in keywords):
                    if cat_name in cat_by_name:
                        tx.category_id = cat_by_name[cat_name]
                        categorized += 1
                        break

        await db.flush()
        steps.append(f"Май: {categorized}/{len(may_tx)} транзакций размечены (вкл. {extra_income} доходов)")

        await db.commit()

    # Verify monthly totals
    async with async_session() as db:
        verify = {}
        for m in range(1, 6):
            month = f"2026-{m:02d}"
            next_month = f"2026-{m+1:02d}-01" if m < 12 else "2027-01-01"
            result = await db.execute(
                select(Transaction.amount).where(
                    Transaction.date >= f"{month}-01",
                    Transaction.date < next_month,
                )
            )
            amounts = [r[0] for r in result.all()]
            income = sum(a for a in amounts if a and a > 0)
            expense = sum(abs(a) for a in amounts if a and a < 0)
            verify[month] = {
                "income": round(income, 2),
                "expense": round(expense, 2),
            }

    return JSONResponse({
        "status": "ok",
        "steps": steps,
        "verify": verify,
    })


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
