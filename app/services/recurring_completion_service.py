from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import select, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recurring import RecurringTask
from app.models.recurring_completion import RecurringCompletion


async def get_completion(
    db: AsyncSession,
    recurring_task_id: int,
    occurrence_date: date,
) -> Optional[RecurringCompletion]:
    result = await db.execute(
        select(RecurringCompletion).where(
            RecurringCompletion.recurring_task_id == recurring_task_id,
            RecurringCompletion.occurrence_date == occurrence_date,
        )
    )
    return result.scalar_one_or_none()


async def get_completed_today_keys(db: AsyncSession, today: date):
    """Пары (title, category_id) регулярных задач, выполненных сегодня."""
    result = await db.execute(
        select(RecurringTask.title, RecurringTask.category_id)
        .join(RecurringCompletion, RecurringCompletion.recurring_task_id == RecurringTask.id)
        .where(
            RecurringCompletion.occurrence_date == today,
            RecurringCompletion.status == "completed",
        )
    )
    return set((row[0], row[1]) for row in result.all())


async def record_completion(
    db: AsyncSession,
    template: RecurringTask,
    occurrence_date: date,
) -> RecurringCompletion:
    """Записать выполнение за день (мерж с существующей записью, без новых Task)."""
    existing = await get_completion(db, template.id, occurrence_date)
    now = datetime.utcnow()

    if existing:
        if existing.status == "completed":
            return existing
        if existing.status == "missed":
            template.missed_count = max(0, (template.missed_count or 0) - 1)
        existing.status = "completed"
        existing.completed_at = now
        template.completed_count = (template.completed_count or 0) + 1
        await db.flush()
        return existing

    row = RecurringCompletion(
        recurring_task_id=template.id,
        occurrence_date=occurrence_date,
        status="completed",
        completed_at=now,
    )
    db.add(row)
    template.completed_count = (template.completed_count or 0) + 1
    await db.flush()
    return row


async def record_missed(
    db: AsyncSession,
    template: RecurringTask,
    occurrence_date: date,
) -> Optional[RecurringCompletion]:
    """Записать пропуск, если за этот день ещё не было выполнения."""
    existing = await get_completion(db, template.id, occurrence_date)
    if existing:
        return None if existing.status == "completed" else existing

    row = RecurringCompletion(
        recurring_task_id=template.id,
        occurrence_date=occurrence_date,
        status="missed",
    )
    db.add(row)
    template.missed_count = (template.missed_count or 0) + 1
    await db.flush()
    return row


async def find_template_for_task(
    db: AsyncSession,
    title: str,
    category_id: Optional[int],
) -> Optional[RecurringTask]:
    result = await db.execute(
        select(RecurringTask).where(
            RecurringTask.title == title,
            RecurringTask.category_id == category_id,
            RecurringTask.is_active == True,
        )
    )
    return result.scalar_one_or_none()


async def sync_template_counters(db: AsyncSession, template: RecurringTask) -> None:
    """Пересчитать счётчики из журнала."""
    completed_r = await db.execute(
        select(sqlfunc.count(RecurringCompletion.id)).where(
            RecurringCompletion.recurring_task_id == template.id,
            RecurringCompletion.status == "completed",
        )
    )
    missed_r = await db.execute(
        select(sqlfunc.count(RecurringCompletion.id)).where(
            RecurringCompletion.recurring_task_id == template.id,
            RecurringCompletion.status == "missed",
        )
    )
    template.completed_count = completed_r.scalar() or 0
    template.missed_count = missed_r.scalar() or 0
