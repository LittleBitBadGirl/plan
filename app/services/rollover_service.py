from datetime import date
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.task import Task
from app.db.database import async_session


async def _rollover_impl(db: AsyncSession):
    """Перенести просроченные задачи на сегодня.

    Исключаем source='recurring' — они эфемерны, генератор создаст свежую копию сам.
    Переносим все задачи независимо от дня недели — пользователь сам решает, что делать.
    """
    today = date.today()

    result = await db.execute(
        select(Task)
        .where(
            Task.status.in_(["новая", "в_работе"]),
            Task.due_date < today,
            Task.is_archived == False,
            Task.source.is_distinct_from("recurring"),  # recurring регенерируются сами
        )
    )
    overdue_tasks = result.scalars().all()

    moved_count = 0
    chronic_count = 0

    for task in overdue_tasks:
        if task.postpones is None:
            task.postpones = 0
        task.postpones += 1
        task.due_date = today

        if task.postpones > 7 and not task.chronic_task:
            task.chronic_task = True
            chronic_count += 1

        moved_count += 1

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
