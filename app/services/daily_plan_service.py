"""Текст плана на день — дашборд и Telegram (/plan, 09:00)."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task
from app.services.calendar_sync_service import (
    get_visible_events_grouped,
    refresh_calendar_events,
)
from app.services.recurring_schedule import get_recurring_templates_for_date


async def refresh_calendar_for_plan() -> None:
    await refresh_calendar_events()


def _format_event_line(ev) -> str:
    if ev.is_all_day:
        time_str = "весь день"
    else:
        time_str = ev.start_at.strftime("%H:%M")
        if ev.end_at:
            time_str += f"–{ev.end_at.strftime('%H:%M')}"
    recur = " 🔁" if ev.is_recurring else ""
    return f"• {time_str} {ev.title}{recur}"


async def build_daily_plan_text(db: AsyncSession, today: date | None = None) -> str | None:
    """
    Собрать план на день. None — если задач, встреч и регулярных нет.
    Порядок: встречи → личное → задачи → регулярные.
    """
    today = today or date.today()

    work_meetings, personal_events = await get_visible_events_grouped(db, today)

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

    recurring_today = await get_recurring_templates_for_date(
        db, today, exclude_completed=True
    )

    if not work_meetings and not personal_events and not tasks and not recurring_today:
        return None

    lines = [f"🌅 Доброе утро! План на сегодня ({today.strftime('%d.%m')}):\n"]

    if work_meetings:
        lines.append("\n📅 Встречи:")
        for ev in work_meetings:
            lines.append(_format_event_line(ev))

    if personal_events:
        lines.append("\n🌿 Личное:")
        for ev in personal_events:
            lines.append(_format_event_line(ev))

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
