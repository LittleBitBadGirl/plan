"""Тесты текста дневного плана."""
from datetime import datetime

import pytest

from app.db.database import async_session
from app.models.calendar_event import CalendarEvent
from app.models.task import Task
from app.services.daily_plan_service import build_daily_plan_text


@pytest.mark.asyncio
async def test_plan_includes_meetings_before_tasks():
    today = datetime(2026, 6, 3).date()

    async with async_session() as db:
        db.add(
            CalendarEvent(
                external_uid="m1",
                calendar_name="группа",
                calendar_url="http://c",
                title="Статус",
                start_at=datetime(2026, 6, 3, 11, 0),
                end_at=datetime(2026, 6, 3, 11, 30),
                planner_visible=True,
            )
        )
        db.add(
            Task(
                title="Отчёт",
                due_date=today,
                status="новая",
                item_kind="task",
            )
        )
        await db.commit()
        text = await build_daily_plan_text(db, today=today)

    assert text is not None
    assert "📅 Встречи" in text
    assert "11:00" in text
    assert "Статус" in text
    assert "🔸 Задачи" in text
    assert "Отчёт" in text
    assert text.index("📅") < text.index("🔸")
