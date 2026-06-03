"""Синхронизация CalDAV → calendar_events."""
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


def _start_of_day(d: date) -> datetime:
    return datetime.combine(d, time.min)


def _end_of_day(d: date) -> datetime:
    return datetime.combine(d, time.max)


# Если в CalDAV нет dtend — считаем слот 30 мин
_DEFAULT_MEETING_MINUTES = 30


def event_is_upcoming(event: CalendarEvent, now: datetime | None = None) -> bool:
    """Встреча ещё не закончилась (для дашборда и /plan)."""
    now = now or datetime.now()
    if event.end_at:
        return event.end_at > now
    return event.start_at + timedelta(minutes=_DEFAULT_MEETING_MINUTES) > now


async def sync_calendar_events() -> dict[str, int]:
    """
    Pull CalDAV и upsert в БД.
    Возвращает счётчики: fetched, upserted, hidden.
    """
    if not settings.calendar_sync_enabled:
        return {"skipped": 1}

    if not settings.yandex_caldav_user or not settings.yandex_caldav_app_password:
        app_logger.warning("Calendar sync: нет учётных данных CalDAV")
        return {"error": 1}

    try:
        rows = await asyncio.to_thread(fetch_calendar_events)
    except Exception as e:
        app_logger.error(f"Calendar sync CalDAV error: {e}")
        return {"error": 1}

    cfg = load_calendar_sync_config()
    sync_cfg = cfg.get("sync", {})
    days_past = sync_cfg.get("horizon_days_past", 1)
    days_future = sync_cfg.get("horizon_days_future", 14)
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

            visible, filter_reason = event_planner_visible(
                row["title"],
                row["start_at"],
                row["calendar_name"],
                cfg,
                force_ignore=user_ignored,
            )
            if not visible:
                hidden += 1

            ignored_at = now if user_ignored else None

            if ev:
                ev.title = row["title"]
                ev.start_at = row["start_at"]
                ev.end_at = row["end_at"]
                ev.location = row["location"]
                ev.calendar_name = row["calendar_name"]
                ev.calendar_url = row["calendar_url"]
                ev.is_recurring = row["is_recurring"]
                ev.recurrence_id = row["recurrence_id"]
                ev.planner_visible = visible
                ev.filter_reason = filter_reason
                ev.last_seen_at = now
                if user_ignored:
                    ev.ignored_at = ev.ignored_at or ignored_at
            else:
                db.add(
                    CalendarEvent(
                        external_uid=uid,
                        recurrence_id=row["recurrence_id"],
                        calendar_name=row["calendar_name"],
                        calendar_url=row["calendar_url"],
                        title=row["title"],
                        start_at=row["start_at"],
                        end_at=row["end_at"],
                        location=row["location"],
                        is_recurring=row["is_recurring"],
                        planner_visible=visible,
                        filter_reason=filter_reason,
                        ignored_at=ignored_at,
                        last_seen_at=now,
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
) -> list[CalendarEvent]:
    """Актуальные встречи на день (прошедшие скрыты, если include_past=False)."""
    start = _start_of_day(day)
    end = _end_of_day(day)
    result = await db.execute(
        select(CalendarEvent)
        .where(
            CalendarEvent.planner_visible == True,  # noqa: E712
            CalendarEvent.start_at >= start,
            CalendarEvent.start_at <= end,
        )
        .order_by(CalendarEvent.start_at.asc())
    )
    events = list(result.scalars().all())
    if include_past:
        return events
    return [e for e in events if event_is_upcoming(e, now)]
