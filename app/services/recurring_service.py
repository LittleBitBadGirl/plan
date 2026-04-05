from datetime import date
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.recurring import RecurringTask
from app.models.task import Task
from app.db.database import async_session
import json


async def _generate_impl(db: AsyncSession):
    """Создать задачи из периодических шаблонов на сегодня"""
    today = date.today()
    weekday_map = {
        0: "mon", 1: "tue", 2: "wed",
        3: "thu", 4: "fri", 5: "sat", 6: "sun"
    }
    today_weekday = weekday_map[today.weekday()]

    result = await db.execute(
        select(RecurringTask).where(RecurringTask.is_active == True)
    )
    templates = result.scalars().all()

    created_count = 0

    for template in templates:
        # Проверить, нужно ли создавать задачу сегодня
        should_create = False

        if template.recurrence_type == "daily":
            should_create = True
        elif template.recurrence_type == "weekly":
            if template.recurrence_days:
                days = json.loads(template.recurrence_days) if isinstance(template.recurrence_days, str) else template.recurrence_days
                should_create = today_weekday in days
        elif template.recurrence_type == "monthly":
            should_create = today.day == template.start_date.day
        elif template.recurrence_type == "custom":
            # Проверить интервал
            days_diff = (today - template.start_date).days
            should_create = days_diff % template.recurrence_interval == 0

        if should_create:
            # Проверить, нет ли уже такой задачи на сегодня
            existing = await db.execute(
                select(Task).where(
                    Task.title == template.title,
                    Task.due_date == today,
                    Task.is_archived == False,
                )
            )
            if existing.scalar_one_or_none():
                continue

            # Создать задачу
            task = Task(
                title=template.title,
                description=template.description,
                category_id=template.category_id,
                priority=template.priority,
                due_date=today,
                source="recurring",
            )
            db.add(task)
            created_count += 1

    await db.flush()

    return {"created": created_count}


async def generate_recurring_tasks(db: AsyncSession = None):
    """Генерация периодических задач (для APScheduler)"""
    if db is None:
        async with async_session() as db:
            result = await _generate_impl(db)
            await db.commit()
            return result
    return await _generate_impl(db)
