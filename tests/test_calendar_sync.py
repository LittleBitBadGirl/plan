"""Тесты синка календаря (mock CalDAV)."""
from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.db.database import async_session
from app.models.calendar_event import CalendarEvent
from app.services.calendar_sync_service import (
    sync_calendar_events,
    get_visible_events_for_day,
    event_is_upcoming,
)


@pytest.mark.asyncio
async def test_sync_upserts_and_filters():
    fake_rows = [
        {
            "external_uid": "yandex:uid-visible-1",
            "is_all_day": False,
            "calendar_source": "yandex",
            "calendar_kind": "work",
            "recurrence_id": None,
            "calendar_name": "встречи внутри группы",
            "calendar_url": "https://caldav.yandex.ru/events-1/",
            "title": "1-1 Лышков",
            "start_at": datetime(2026, 6, 3, 12, 0),
            "end_at": datetime(2026, 6, 3, 12, 30),
            "location": None,
            "is_recurring": False,
        },
        {
            "external_uid": "yandex:uid-hidden-frontend",
            "is_all_day": False,
            "calendar_source": "yandex",
            "calendar_kind": "work",
            "recurrence_id": "uid-hidden-frontend",
            "calendar_name": "встречи внутри группы",
            "calendar_url": "https://caldav.yandex.ru/events-1/",
            "title": "Лидирование команды Frontend",
            "start_at": datetime(2026, 6, 5, 14, 30),
            "end_at": datetime(2026, 6, 5, 15, 0),
            "location": None,
            "is_recurring": True,
        },
    ]

    with patch("app.services.calendar_sync_service.settings") as mock_settings:
        mock_settings.calendar_sync_enabled = True
        mock_settings.yandex_caldav_user = "test@dalee.ru"
        mock_settings.yandex_caldav_app_password = "secret"
        mock_settings.google_calendar_sync_enabled = False
        mock_settings.google_calendar_ical_url = ""
        with patch(
            "app.services.calendar_sync_service._fetch_all_provider_rows",
            return_value=fake_rows,
        ):
            result = await sync_calendar_events()

    assert result.get("upserted") == 2

    async with async_session() as db:
        visible = await get_visible_events_for_day(
            db,
            datetime(2026, 6, 3).date(),
            now=datetime(2026, 6, 3, 11, 0),
        )
        assert len(visible) == 1
        assert visible[0].title == "1-1 Лышков"

        hidden = await db.execute(
            select(CalendarEvent).where(
                CalendarEvent.external_uid == "yandex:uid-hidden-frontend"
            )
        )
        row = hidden.scalar_one()
        assert row.planner_visible is False
        assert row.filter_reason is not None


@pytest.mark.asyncio
async def test_past_meetings_hidden_from_dashboard():
    day = datetime(2026, 6, 3).date()
    async with async_session() as db:
        db.add(
            CalendarEvent(
                external_uid="yandex:past-1",
                calendar_name="группа",
                calendar_url="http://c",
                title="Утренний статус",
                start_at=datetime(2026, 6, 3, 9, 0),
                end_at=datetime(2026, 6, 3, 9, 30),
                planner_visible=True,
                calendar_source="yandex",
                calendar_kind="work",
            )
        )
        db.add(
            CalendarEvent(
                external_uid="yandex:future-1",
                calendar_name="группа",
                calendar_url="http://c",
                title="Созвон после обеда",
                start_at=datetime(2026, 6, 3, 15, 0),
                end_at=datetime(2026, 6, 3, 16, 0),
                planner_visible=True,
                calendar_source="yandex",
                calendar_kind="work",
            )
        )
        await db.commit()

        visible = await get_visible_events_for_day(
            db, day, now=datetime(2026, 6, 3, 12, 0), calendar_kind="work"
        )
        assert len(visible) == 1
        assert visible[0].title == "Созвон после обеда"

    ev = CalendarEvent(
        external_uid="x",
        calendar_name="g",
        calendar_url="u",
        title="t",
        start_at=datetime(2026, 6, 3, 10, 0),
        end_at=datetime(2026, 6, 3, 11, 0),
    )
    assert event_is_upcoming(ev, datetime(2026, 6, 3, 10, 30)) is True
    assert event_is_upcoming(ev, datetime(2026, 6, 3, 11, 1)) is False
