"""Синхронизация CalDAV (Яндекс) и iCal (Google) → calendar_events."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta

from sqlalchemy import and_, select

from app.config import settings
from app.db.database import async_session
from app.models.calendar_event import CalendarEvent
from app.services.calendar_caldav import fetch_calendar_events
from app.services.calendar_filter_service import event_planner_visible, load_calendar_sync_config
from app.services.calendar_ignore_service import event_matches_any_rule, load_ignore_rules
from app.utils.logger import app_logger

_sync_lock = asyncio.Lock()


def _start_of_day(d: date) -> datetime:
    return datetime.combine(d, time.min)


def _end_of_day(d: date) -> datetime:
    return datetime.combine(d, time.max)


# Если в CalDAV нет dtend — считаем слот 30 мин
_DEFAULT_MEETING_MINUTES = 30


def event_is_upcoming(event: CalendarEvent, now: datetime | None = None) -> bool:
    """Рабочая встреча ещё не закончилась (для дашборда и /plan)."""
    now = now or datetime.now()
    if event.end_at:
        return event.end_at > now
    return event.start_at + timedelta(minutes=_DEFAULT_MEETING_MINUTES) > now


def event_visible_on_day(
    event: CalendarEvent,
    day: date,
    now: datetime | None = None,
) -> bool:
    """Показывать событие в плане на день day."""
    if event.start_at.date() != day:
        return False
    if event.calendar_kind == "personal":
        return True
    return event_is_upcoming(event, now)


async def _fetch_all_provider_rows() -> list[dict]:
    rows: list[dict] = []
    if settings.calendar_sync_enabled and settings.yandex_caldav_user and settings.yandex_caldav_app_password:
        rows.extend(await asyncio.to_thread(fetch_calendar_events))
    if settings.google_calendar_sync_enabled and settings.google_calendar_ical_url:
        from app.services.calendar_google import fetch_google_calendar_events

        rows.extend(await asyncio.to_thread(fetch_google_calendar_events))
    return rows


def calendar_sync_active() -> bool:
    yandex = (
        settings.calendar_sync_enabled
        and settings.yandex_caldav_user
        and settings.yandex_caldav_app_password
    )
    google = settings.google_calendar_sync_enabled and bool(
        settings.google_calendar_ical_url
    )
    return yandex or google


def _calendar_sync_active() -> bool:
    return calendar_sync_active()


async def refresh_calendar_events(timeout: float = 45.0) -> dict[str, int]:
    """Синхронизация календарей с mutex — параллельные вызовы не дублируют fetch."""
    if not calendar_sync_active():
        return {"skipped": 1}

    if _sync_lock.locked():
        return {"skipped": 1, "in_progress": 1}

    async with _sync_lock:
        return await asyncio.wait_for(sync_calendar_events(), timeout=timeout)


async def sync_calendar_events() -> dict[str, int]:
    """
    Pull CalDAV + Google iCal и upsert в БД.
    Возвращает счётчики: fetched, upserted, hidden.
    """
    if not _calendar_sync_active():
        return {"skipped": 1}

    try:
        rows = await _fetch_all_provider_rows()
    except Exception as e:
        app_logger.error(f"Calendar sync error: {e}")
        return {"error": 1}

    cfg = load_calendar_sync_config()
    sync_cfg = cfg.get("sync", {})
    days_past = sync_cfg.get("horizon_days_past", 1)
    days_future = sync_cfg.get("horizon_days_future", 14)
    if settings.google_calendar_sync_enabled:
        days_future = max(
            days_future,
            cfg.get("google", {}).get("horizon_days_future", days_future),
        )
    window_start = _start_of_day(date.today() - timedelta(days=days_past))
    window_end = _end_of_day(date.today() + timedelta(days=days_future))

    now = datetime.now()
    seen_uids: set[str] = set()
    upserted = 0
    hidden = 0

    async with async_session() as db:
        ignore_rules = await load_ignore_rules(db)

        for row in rows:
            uid = row["external_uid"]
            seen_uids.add(uid)

            existing = await db.execute(
                select(CalendarEvent).where(CalendarEvent.external_uid == uid)
            )
            ev = existing.scalar_one_or_none()

            user_ignored = event_matches_any_rule(
                external_uid=uid,
                title=row["title"],
                recurrence_id=row.get("recurrence_id"),
                rules=ignore_rules,
            ) or (ev is not None and ev.ignored_at is not None)

            calendar_kind = row.get("calendar_kind", "work")
            visible, filter_reason = event_planner_visible(
                row["title"],
                row["start_at"],
                row["calendar_name"],
                cfg,
                force_ignore=user_ignored,
                calendar_kind=calendar_kind,
            )
            if not visible:
                hidden += 1

            ignored_at = now if user_ignored else None

            fields = {
                "title": row["title"],
                "start_at": row["start_at"],
                "end_at": row["end_at"],
                "location": row["location"],
                "calendar_name": row["calendar_name"],
                "calendar_url": row["calendar_url"],
                "is_recurring": row["is_recurring"],
                "is_all_day": row.get("is_all_day", False),
                "calendar_source": row.get("calendar_source", "yandex"),
                "calendar_kind": calendar_kind,
                "recurrence_id": row.get("recurrence_id"),
                "planner_visible": visible,
                "filter_reason": filter_reason,
                "last_seen_at": now,
            }

            if ev:
                for key, val in fields.items():
                    setattr(ev, key, val)
                if user_ignored:
                    ev.ignored_at = ev.ignored_at or ignored_at
            else:
                db.add(
                    CalendarEvent(
                        external_uid=uid,
                        ignored_at=ignored_at,
                        **fields,
                    )
                )
            upserted += 1

        if seen_uids:
            stale_result = await db.execute(
                select(CalendarEvent).where(
                    and_(
                        CalendarEvent.external_uid.not_in(seen_uids),
                        CalendarEvent.start_at >= window_start,
                        CalendarEvent.start_at <= window_end,
                        CalendarEvent.ignored_at.is_(None),
                    )
                )
            )
            for stale in stale_result.scalars().all():
                stale.planner_visible = False
                stale.filter_reason = "stale"

        await db.commit()

    app_logger.info(
        f"Calendar sync: fetched={len(rows)} upserted={upserted} hidden={hidden}"
    )
    return {"fetched": len(rows), "upserted": upserted, "hidden": hidden}


async def get_visible_events_for_day(
    db,
    day: date,
    *,
    include_past: bool = False,
    now: datetime | None = None,
    calendar_kind: str | None = None,
) -> list[CalendarEvent]:
    """Актуальные события на день. work — скрываются после end; personal — весь день."""
    start = _start_of_day(day)
    end = _end_of_day(day)
    query = (
        select(CalendarEvent)
        .where(
            CalendarEvent.planner_visible == True,  # noqa: E712
            CalendarEvent.start_at >= start,
            CalendarEvent.start_at <= end,
        )
        .order_by(CalendarEvent.start_at.asc())
    )
    if calendar_kind:
        query = query.where(CalendarEvent.calendar_kind == calendar_kind)

    result = await db.execute(query)
    events = list(result.scalars().all())
    if include_past:
        return events
    return [e for e in events if event_visible_on_day(e, day, now)]


async def get_visible_events_grouped(
    db,
    day: date,
    *,
    now: datetime | None = None,
) -> tuple[list[CalendarEvent], list[CalendarEvent]]:
    """(рабочие встречи, личные напоминания) на день."""
    work = await get_visible_events_for_day(
        db, day, now=now, calendar_kind="work"
    )
    personal = await get_visible_events_for_day(
        db, day, now=now, calendar_kind="personal"
    )
    return work, personal
