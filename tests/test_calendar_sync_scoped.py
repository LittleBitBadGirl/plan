"""Синк календаря ограничен своим календарём.

Кнопка обновления стоит у каждого блока: у «Встреч» — своя, у «Личного» — своя.
Значит синк рабочего календаря не должен трогать личные события: иначе кнопка у
«Встреч» помечает личные события пропавшими (filter_reason="stale") просто
потому, что их uid не пришли в рабочей выборке.
"""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.calendar_event import CalendarEvent
from app.services import calendar_sync_service as svc


def _row(uid: str, kind: str, title: str) -> dict:
    start = datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=10)
    return {
        "external_uid": uid,
        "title": title,
        "start_at": start,
        "end_at": start + timedelta(hours=1),
        "location": None,
        "calendar_name": "Рабочий" if kind == "work" else "Личный",
        "calendar_url": "https://example.com/cal.ics",
        "is_recurring": False,
        "is_all_day": False,
        "calendar_source": "yandex",
        "calendar_kind": kind,
        "recurrence_id": None,
    }


@pytest.mark.asyncio
async def test_sync_work_kind_does_not_touch_personal(db, monkeypatch):
    start = datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=9)
    db.add(
        CalendarEvent(
            external_uid="p-old",
            title="Личное событие",
            start_at=start,
            end_at=start + timedelta(hours=1),
            calendar_name="Личный",
            calendar_url="https://example.com/personal.ics",
            calendar_kind="personal",
            planner_visible=True,
        )
    )
    await db.commit()

    rows = [_row("w-new", "work", "Рабочая встреча"), _row("p-new", "personal", "Личное новое")]

    async def fake_fetch():
        return rows

    monkeypatch.setattr(svc, "_fetch_all_provider_rows", fake_fetch)
    monkeypatch.setattr(svc, "_calendar_sync_active", lambda: True)

    result = await svc.sync_calendar_events(kind="work")
    assert result["upserted"] == 1  # взяли только рабочее событие

    await db.commit()
    uids = {e.external_uid for e in (await db.execute(select(CalendarEvent))).scalars().all()}
    assert "w-new" in uids            # рабочий синк приехал
    assert "p-new" not in uids        # личные события этим вызовом не тянулись

    personal = (
        await db.execute(select(CalendarEvent).where(CalendarEvent.external_uid == "p-old"))
    ).scalar_one()
    assert personal.planner_visible is True       # личное не спрятано
    assert personal.filter_reason != "stale"      # и не объявлено пропавшим


@pytest.mark.asyncio
async def test_sync_without_kind_still_pulls_everything(db, monkeypatch):
    rows = [_row("w-all", "work", "Рабочая"), _row("p-all", "personal", "Личная")]

    async def fake_fetch():
        return rows

    monkeypatch.setattr(svc, "_fetch_all_provider_rows", fake_fetch)
    monkeypatch.setattr(svc, "_calendar_sync_active", lambda: True)

    result = await svc.sync_calendar_events()
    assert result["upserted"] == 2
    await db.commit()
    uids = {e.external_uid for e in (await db.execute(select(CalendarEvent))).scalars().all()}
    assert {"w-all", "p-all"} <= uids


@pytest.mark.asyncio
async def test_both_calendars_have_own_sync_button(client, monkeypatch):
    from app.web.routes import calendar as calendar_routes

    async def no_rows():
        return []  # кнопку проверяем, к провайдеру в тесте не ходим

    monkeypatch.setattr(svc, "_fetch_all_provider_rows", no_rows)
    monkeypatch.setattr(calendar_routes, "calendar_sync_active", lambda: True)

    resp = await client.post("/api/calendar/sync?kind=work")
    assert resp.status_code == 200
    html = resp.text
    assert "/api/calendar/sync?kind=work" in html
    assert "/api/calendar/sync?kind=personal" in html
    assert html.count("Обновление календаря") == 2  # по кнопке на каждый блок


@pytest.mark.asyncio
async def test_sync_rejects_unknown_kind(client):
    resp = await client.post("/api/calendar/sync?kind=market")
    assert resp.status_code == 400
