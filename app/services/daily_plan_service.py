"""Текст плана на день — дашборд и Telegram (/plan, 09:00)."""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.recurring import RecurringTask
from app.models.task import Task
from app.services.calendar_sync_service import get_visible_events_for_day, sync_calendar_events
from app.services.recurring_completion_service import get_completed_today_keys


async def refresh_calendar_for_plan() -> None:
    if settings.calendar_sync_enabled:
        await sync_calendar_events()


async def build_daily_plan_text(db: AsyncSession, today: date | None = None) -> str | None:
    """
    Собрать план на день. None — если задач, встреч и регулярных нет.
    Порядок: встречи → задачи → регулярные (как колонка 3 над «Регулярными»).
    """
    today = today or date.today()
    weekday_map = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
    today_weekday = weekday_map[today.weekday()]

    meetings = await get_visible_events_for_day(db, today)

    task_result = await db.execute(
        select(Task)
        .where(
            Task.due_date == today,
            Task.status.in_(["новая", "в_работе"]),
            Task.is_archived == False,
            Task.parent_task_id.is_(None),
            Task.source.is_distinct_from("recurring"),
            Task.item_kind == "task",
        )
        .order_by(Task.sort_order.asc(), Task.due_time.asc().nulls_last())
    )
    tasks = list(task_result.scalars().all())

    recur_result = await db.execute(
        select(RecurringTask).where(RecurringTask.is_active == True)
    )
    all_recurring = recur_result.scalars().all()
    completed_today = await get_completed_today_keys(db, today)

    recurring_today: list[RecurringTask] = []
    for rt in all_recurring:
        if (rt.title, rt.category_id) in completed_today:
            continue
        if rt.end_date and today > rt.end_date:
            continue
        if today < rt.start_date:
            continue
        if rt.recurrence_type == "daily":
            recurring_today.append(rt)
        elif rt.recurrence_type == "weekly":
            days = rt.recurrence_days
            if isinstance(days, str):
                try:
                    days = json.loads(days)
                except Exception:
                    days = []
            if days and today_weekday in days:
                recurring_today.append(rt)
        elif rt.recurrence_type == "monthly":
            if today.day == rt.start_date.day:
                recurring_today.append(rt)

    if not meetings and not tasks and not recurring_today:
        return None

    lines = [f"🌅 Доброе утро! План на сегодня ({today.strftime('%d.%m')}):\n"]

    if meetings:
        lines.append("\n📅 Встречи:")
        for ev in meetings:
            time_str = ev.start_at.strftime("%H:%M")
            if ev.end_at:
                time_str += f"–{ev.end_at.strftime('%H:%M')}"
            recur = " 🔁" if ev.is_recurring else ""
            lines.append(f"• {time_str} {ev.title}{recur}")

    if tasks:
        lines.append("\n🔸 Задачи:")
        for t in tasks:
            time_str = f" {t.due_time.strftime('%H:%M')}" if t.due_time else ""
            lines.append(f"•{time_str} {t.title}")

    if recurring_today:
        lines.append("\n🔄 Регулярные:")
        for rt in recurring_today:
            lines.append(f"• {rt.title}")

    lines.append("\nХорошего дня! 🚀")
    return "\n".join(lines)
