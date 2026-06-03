from datetime import date
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.recurring import RecurringTask
from app.models.task import Task
from app.db.database import async_session
from app.services.recurring_completion_service import (
    find_template_for_task,
    record_missed,
)
import json


async def _generate_impl(db: AsyncSession):
    """Создать задачи из периодических шаблонов на сегодня."""
    today = date.today()

    # Просроченные recurring-вхождения → архив + журнал пропусков
    overdue_result = await db.execute(
        select(Task).where(
            Task.source == "recurring",
            Task.due_date < today,
            Task.is_archived == False,
        )
    )
    for overdue_task in overdue_result.scalars().all():
        template = await find_template_for_task(
            db, overdue_task.title, overdue_task.category_id
        )
        if template and overdue_task.due_date:
            await record_missed(db, template, overdue_task.due_date)

    await db.execute(
        update(Task)
        .where(
            Task.source == "recurring",
            Task.due_date < today,
            Task.is_archived == False,
        )
        .values(is_archived=True)
    )

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
            days_diff = (today - template.start_date).days
            should_create = days_diff % template.recurrence_interval == 0

        if should_create:
            existing = await db.execute(
                select(Task).where(
                    Task.title == template.title,
                    Task.due_date == today,
                    Task.is_archived == False,
                )
            )
            if existing.scalar_one_or_none():
                continue

            task = Task(
                title=template.title,
                description=template.description,
                category_id=template.category_id,
                priority=template.priority,
                due_date=today,
                source="recurring",
                item_kind="task",
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
