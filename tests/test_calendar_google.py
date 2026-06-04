"""Тесты Google iCal sync и видимости личных событий."""
from datetime import datetime
from unittest.mock import patch

import pytest

from app.db.database import async_session
from app.models.calendar_event import CalendarEvent
from app.services.calendar_sync_service import (
    event_visible_on_day,
    get_visible_events_for_day,
    get_visible_events_grouped,
    sync_calendar_events,
)


@pytest.mark.asyncio
async def test_sync_google_personal_visible_all_day():
    fake_rows = [
        {
            "external_uid": "google:uid1@20260604T090000",
            "recurrence_id": "uid1",
            "calendar_name": "Личный",
            "calendar_url": "https://calendar.google.com/",
            "title": "Др Яны",
            "start_at": datetime(2026, 6, 4, 9, 0),
            "end_at": datetime(2026, 6, 4, 10, 0),
            "location": None,
            "is_recurring": True,
            "is_all_day": False,
            "calendar_source": "google",
            "calendar_kind": "personal",
        },
    ]

    with patch("app.services.calendar_sync_service.settings") as mock_settings:
        mock_settings.calendar_sync_enabled = False
        mock_settings.yandex_caldav_user = ""
        mock_settings.yandex_caldav_app_password = ""
        mock_settings.google_calendar_sync_enabled = True
        mock_settings.google_calendar_ical_url = "https://example.com/basic.ics"
        with patch(
            "app.services.calendar_sync_service._fetch_all_provider_rows",
            return_value=fake_rows,
        ):
            result = await sync_calendar_events()

    assert result.get("upserted") == 1

    day = datetime(2026, 6, 4).date()
    async with async_session() as db:
        personal = await get_visible_events_for_day(
            db,
            day,
            now=datetime(2026, 6, 4, 18, 0),
            calendar_kind="personal",
        )
        assert len(personal) == 1
        assert personal[0].title == "Др Яны"

        work, pers = await get_visible_events_grouped(
            db, day, now=datetime(2026, 6, 4, 18, 0)
        )
        assert len(work) == 0
        assert len(pers) == 1


@pytest.mark.asyncio
async def test_work_meeting_hidden_after_end():
    day = datetime(2026, 6, 4).date()
    ev = CalendarEvent(
        external_uid="yandex:past-1",
        calendar_name="группа",
        calendar_url="http://c",
        title="Созвон",
        start_at=datetime(2026, 6, 4, 9, 0),
        end_at=datetime(2026, 6, 4, 9, 30),
        planner_visible=True,
        calendar_source="yandex",
        calendar_kind="work",
    )
    personal = CalendarEvent(
        external_uid="google:p1@20260604T090000",
        calendar_name="Личный",
        calendar_url="http://g",
        title="Купить молоко",
        start_at=datetime(2026, 6, 4, 9, 0),
        end_at=datetime(2026, 6, 4, 9, 30),
        planner_visible=True,
        calendar_source="google",
        calendar_kind="personal",
    )
    now = datetime(2026, 6, 4, 12, 0)
    assert event_visible_on_day(ev, day, now) is False
    assert event_visible_on_day(personal, day, now) is True

    async with async_session() as db:
        db.add(ev)
        db.add(personal)
        await db.commit()

        visible_work = await get_visible_events_for_day(
            db, day, now=now, calendar_kind="work"
        )
        visible_personal = await get_visible_events_for_day(
            db, day, now=now, calendar_kind="personal"
        )
        assert len(visible_work) == 0
        assert len(visible_personal) == 1
