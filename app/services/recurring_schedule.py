"""Единые правила: какие периодические шаблоны попадают на конкретную дату."""
from __future__ import annotations

import json
from datetime import date
from typing import Iterable, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recurring import RecurringTask

WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
CompletionKey = Tuple[str, Optional[int]]


def weekday_name(day: date) -> str:
    return WEEKDAY_NAMES[day.weekday()]


def parse_recurrence_days(days) -> list[str]:
    if not days:
        return []
    if isinstance(days, str):
        try:
            parsed = json.loads(days)
            return list(parsed) if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    if isinstance(days, list):
        return list(days)
    return []


def recurring_applies_on_date(template: RecurringTask, day: date) -> bool:
    """
    Срабатывает ли расписание шаблона на дату day.
    Не учитывает is_active и факт выполнения — только календарная логика.
    """
    if day < template.start_date:
        return False
    if template.end_date and day > template.end_date:
        return False

    rtype = template.recurrence_type
    if rtype == "daily":
        return True
    if rtype == "weekly":
        days = parse_recurrence_days(template.recurrence_days)
        return bool(days) and weekday_name(day) in days
    if rtype == "monthly":
        return day.day == template.start_date.day
    if rtype == "custom":
        interval = template.recurrence_interval or 1
        if interval <= 0:
            return False
        days_diff = (day - template.start_date).days
        return days_diff >= 0 and days_diff % interval == 0
    return False


def filter_recurring_templates(
    templates: Iterable[RecurringTask],
    day: date,
    *,
    exclude_completed_keys: Optional[Set[CompletionKey]] = None,
) -> list[RecurringTask]:
    """Отфильтровать уже загруженные шаблоны (без запроса в БД)."""
    out: list[RecurringTask] = []
    for rt in templates:
        if exclude_completed_keys and (rt.title, rt.category_id) in exclude_completed_keys:
            continue
        if recurring_applies_on_date(rt, day):
            out.append(rt)
    return out


async def load_active_recurring_templates(db: AsyncSession) -> list[RecurringTask]:
    result = await db.execute(
        select(RecurringTask).where(RecurringTask.is_active == True)  # noqa: E712
    )
    return list(result.scalars().all())


async def get_recurring_templates_for_date(
    db: AsyncSession,
    day: date,
    *,
    exclude_completed: bool = False,
) -> list[RecurringTask]:
    """
    Активные периодические шаблоны на дату.
    exclude_completed=True — убрать уже отмеченные сегодня (дашборд, утренний план).
    """
    templates = await load_active_recurring_templates(db)
    completed_keys: Optional[Set[CompletionKey]] = None
    if exclude_completed:
        from app.services.recurring_completion_service import get_completed_today_keys

        completed_keys = await get_completed_today_keys(db, day)
    return filter_recurring_templates(
        templates, day, exclude_completed_keys=completed_keys
    )
