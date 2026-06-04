"""Тесты единого расписания периодических задач."""
from datetime import date

import pytest

from app.models.recurring import RecurringTask
from app.services.recurring_schedule import (
    get_recurring_templates_for_date,
    parse_recurrence_days,
    recurring_applies_on_date,
)


def _template(**kwargs) -> RecurringTask:
    defaults = {
        "title": "Тест",
        "recurrence_type": "daily",
        "start_date": date(2026, 1, 1),
        "is_active": True,
    }
    defaults.update(kwargs)
    return RecurringTask(**defaults)


def test_parse_recurrence_days_json_string():
    assert parse_recurrence_days('["mon", "wed"]') == ["mon", "wed"]


def test_daily_always_after_start():
    t = _template(recurrence_type="daily")
    assert recurring_applies_on_date(t, date(2026, 6, 3)) is True
    assert recurring_applies_on_date(t, date(2025, 12, 31)) is False


def test_weekly_monday():
    t = _template(
        recurrence_type="weekly",
        recurrence_days=["mon", "fri"],
    )
    assert recurring_applies_on_date(t, date(2026, 6, 1)) is True  # Mon
    assert recurring_applies_on_date(t, date(2026, 6, 3)) is False  # Wed


def test_monthly_same_day_of_month():
    t = _template(recurrence_type="monthly", start_date=date(2026, 1, 15))
    assert recurring_applies_on_date(t, date(2026, 6, 15)) is True
    assert recurring_applies_on_date(t, date(2026, 6, 16)) is False


def test_custom_interval():
    t = _template(
        recurrence_type="custom",
        start_date=date(2026, 6, 1),
        recurrence_interval=3,
    )
    assert recurring_applies_on_date(t, date(2026, 6, 1)) is True
    assert recurring_applies_on_date(t, date(2026, 6, 4)) is True
    assert recurring_applies_on_date(t, date(2026, 6, 5)) is False


def test_end_date_excludes():
    t = _template(end_date=date(2026, 6, 2))
    assert recurring_applies_on_date(t, date(2026, 6, 2)) is True
    assert recurring_applies_on_date(t, date(2026, 6, 3)) is False


@pytest.mark.asyncio
async def test_get_templates_excludes_completed(db):
    from app.models.category import Category

    cat = Category(name="Тест", type="task")
    db.add(cat)
    await db.flush()

    rt = RecurringTask(
        title="Йога",
        recurrence_type="daily",
        start_date=date.today(),
        category_id=cat.id,
        is_active=True,
    )
    db.add(rt)
    await db.flush()

    from app.models.recurring_completion import RecurringCompletion

    db.add(
        RecurringCompletion(
            recurring_task_id=rt.id,
            occurrence_date=date.today(),
            status="completed",
        )
    )
    await db.commit()

    with_completed = await get_recurring_templates_for_date(
        db, date.today(), exclude_completed=False
    )
    without_completed = await get_recurring_templates_for_date(
        db, date.today(), exclude_completed=True
    )
    assert len(with_completed) == 1
    assert len(without_completed) == 0


@pytest.mark.asyncio
async def test_plan_and_generator_same_set(db):
    """Генератор и план (без completed) видят один и тот же набор на дату."""
    from app.services.daily_plan_service import build_daily_plan_text
    from app.services.recurring_schedule import get_recurring_templates_for_date

    day = date(2026, 6, 4)  # Thursday
    db.add(
        RecurringTask(
            title="Пн-ср",
            recurrence_type="weekly",
            recurrence_days=["mon", "wed"],
            start_date=date(2026, 1, 1),
            is_active=True,
        )
    )
    db.add(
        RecurringTask(
            title="Каждый день",
            recurrence_type="daily",
            start_date=date(2026, 1, 1),
            is_active=True,
        )
    )
    await db.commit()

    templates = await get_recurring_templates_for_date(db, day, exclude_completed=False)
    titles = {t.title for t in templates}
    assert titles == {"Каждый день"}

    text = await build_daily_plan_text(db, today=day)
    assert text is not None
    assert "Каждый день" in text
    assert "Пн-ср" not in text
