from datetime import date
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.task import Task
from app.db.database import async_session


async def _rollover_impl(db: AsyncSession):
    """Перенести просроченные задачи на сегодня"""
    today = date.today()

    # Найти просроченные задачи (статус "новая" или "в_работе", due_date < сегодня)
    result = await db.execute(
        select(Task).where(
            Task.status.in_(["новая", "в_работе"]),
            Task.due_date < today,
            Task.is_archived == False,
        )
    )
    overdue_tasks = result.scalars().all()

    moved_count = 0
    chronic_count = 0

    for task in overdue_tasks:
        # Увеличить счётчик переносов
        task.postpones += 1
        task.due_date = today

        # Если переносов > 7 — хроническая задача
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
