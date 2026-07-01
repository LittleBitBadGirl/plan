from datetime import date
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.task import Task
from app.models.category import Category
from app.db.database import async_session
from app.services.postpones_service import apply_rollover, WORK_CATEGORY_NAMES


async def _rollover_impl(db: AsyncSession):
    """Перенести просроченные задачи на сегодня.

    Исключаем source='recurring' — они эфемерны, генератор создаст свежую копию сам.
    Для рабочих категорий счётчик переносов считает только рабочие дни (пн–пт).
    """
    today = date.today()

    result = await db.execute(
        select(Task)
        .options(selectinload(Task.category))
        .where(
            Task.status.in_(["новая", "в_работе"]),
            Task.due_date < today,
            Task.is_archived == False,
            Task.source.is_distinct_from("recurring"),
        )
    )
    overdue_tasks = result.scalars().all()

    moved_count = 0
    chronic_count = 0
    chronic_before = {t.id: t.chronic_task for t in overdue_tasks}

    for task in overdue_tasks:
        cat_name = task.category.name if task.category else ""
        is_work = cat_name in WORK_CATEGORY_NAMES
        apply_rollover(task, today, is_work_category=is_work)
        moved_count += 1
        if task.chronic_task and not chronic_before.get(task.id):
            chronic_count += 1

    await db.flush()

    return {
        "moved": moved_count,
        "new_chronic": chronic_count,
    }


async def rollover_overdue_tasks(db: AsyncSession = None):
    """Перенести просроченные задачи на сегодня (для APScheduler)"""
    if db is None:
        async with async_session() as db:
            result = await _rollover_impl(db)
            await db.commit()
            return result
    return await _rollover_impl(db)
