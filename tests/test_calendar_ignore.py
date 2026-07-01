"""Тесты «Не пойду» для календарных встреч."""
from datetime import datetime

import pytest
from sqlalchemy import select

from app.db.database import async_session
from app.models.calendar_event import CalendarEvent
from app.models.calendar_ignore_rule import CalendarIgnoreRule
from app.services.calendar_ignore_service import (
    decline_calendar_event,
    event_matches_rule,
    resolve_ignore_target,
)


def test_resolve_ignore_recurring_series_title():
    ev = CalendarEvent(
        external_uid="inst-1",
        recurrence_id=None,
        calendar_name="группа",
        calendar_url="http://x",
        title="Лидирование команды Frontend",
        start_at=datetime(2026, 6, 5, 14, 30),
        is_recurring=True,
    )
    assert resolve_ignore_target(ev) == ("series_title", "Лидирование команды Frontend")


def test_resolve_ignore_single():
    ev = CalendarEvent(
        external_uid="once-99",
        recurrence_id=None,
        calendar_name="группа",
        calendar_url="http://x",
        title="1-1",
        start_at=datetime(2026, 6, 3, 12, 0),
        is_recurring=False,
    )
    assert resolve_ignore_target(ev) == ("external_uid", "once-99")


def test_event_matches_recurrence_id():
    assert event_matches_rule(
        external_uid="child-uid",
        title="Daily",
        recurrence_id="master-uid",
        rule_type="recurrence_id",
        value="master-uid",
    )


@pytest.mark.asyncio
async def test_decline_hides_series_instances():
    async with async_session() as db:
        for i, day in enumerate([5, 12, 19]):
            db.add(
                CalendarEvent(
                    external_uid=f"fe-{i}",
                    recurrence_id=None,
                    calendar_name="встречи внутри группы",
                    calendar_url="http://c",
                    title="Лидирование команды Frontend",
                    start_at=datetime(2026, 6, day, 14, 30),
                    is_recurring=True,
                    planner_visible=True,
                )
            )
        await db.commit()

        first = await db.execute(
            select(CalendarEvent).where(CalendarEvent.external_uid == "fe-0")
        )
        event = first.scalar_one()
        result = await decline_calendar_event(db, event.id)

    assert result["scope"] == "series"
    assert result["hidden"] >= 3

    async with async_session() as db:
        rows = await db.execute(
            select(CalendarEvent).where(CalendarEvent.external_uid.like("fe-%"))
        )
        for ev in rows.scalars().all():
            assert ev.planner_visible is False
            assert ev.filter_reason == "user_ignore"

        rules = await db.execute(select(CalendarIgnoreRule))
        rule = rules.scalar_one()
        assert rule.rule_type == "series_title"
