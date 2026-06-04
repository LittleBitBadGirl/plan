"""Чтение событий из Yandex CalDAV (синхронный слой — вызывать через asyncio.to_thread)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import unquote

from app.config import settings
from app.services.calendar_filter_service import calendar_included, load_calendar_sync_config


def _parse_dt(value: date | datetime) -> datetime:
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, datetime.min.time())
    if getattr(value, "tzinfo", None):
        return value.replace(tzinfo=None)
    return value


def _extract_event_row(ev: Any, calendar_name: str, calendar_url: str) -> dict[str, Any] | None:
    try:
        vevent = ev.vobject_instance.vevent
    except Exception:
        return None

    title = "(без названия)"
    if hasattr(vevent, "summary"):
        title = str(vevent.summary.value)

    start = _parse_dt(vevent.dtstart.value)
    end = None
    if hasattr(vevent, "dtend"):
        end = _parse_dt(vevent.dtend.value)

    uid = str(vevent.uid.value) if hasattr(vevent, "uid") else ""
    if not uid:
        return None

    recurrence_id = None
    if hasattr(vevent, "recurrence_id"):
        recurrence_id = str(vevent.recurrence_id.value)
    elif hasattr(vevent, "rrule"):
        recurrence_id = uid

    location = None
    if hasattr(vevent, "location"):
        location = str(vevent.location.value)[:500]

    return {
        "external_uid": f"yandex:{uid}",
        "recurrence_id": recurrence_id,
        "calendar_name": calendar_name,
        "calendar_url": calendar_url,
        "title": title[:500],
        "start_at": start,
        "end_at": end,
        "location": location,
        "is_recurring": hasattr(vevent, "rrule"),
        "is_all_day": False,
        "calendar_source": "yandex",
        "calendar_kind": "work",
    }


def _search_events(cal: Any, start_dt: datetime, end_dt: datetime) -> list:
    search = getattr(cal, "search", None)
    if callable(search):
        return search(start=start_dt, end=end_dt, event=True, expand=True)
    return cal.date_search(start=start_dt, end=end_dt, expand=True)


def fetch_calendar_events(
    *,
    days_past: int | None = None,
    days_future: int | None = None,
) -> list[dict[str, Any]]:
    """Скачать события из настроенных календарей. Raises on auth/connection errors."""
    try:
        import caldav
    except ImportError as e:
        raise RuntimeError("Пакет caldav не установлен (pip install caldav vobject)") from e

    user = settings.yandex_caldav_user
    password = settings.yandex_caldav_app_password
    if not user or not password:
        return []

    cfg = load_calendar_sync_config()
    sync_cfg = cfg.get("sync", {})
    days_past = days_past if days_past is not None else sync_cfg.get("horizon_days_past", 1)
    days_future = days_future if days_future is not None else sync_cfg.get("horizon_days_future", 14)

    today = date.today()
    start_dt = datetime.combine(today - timedelta(days=days_past), datetime.min.time())
    end_dt = datetime.combine(today + timedelta(days=days_future), datetime.max.time())

    configured_urls = {
        unquote(u.strip()) for u in settings.yandex_calendar_urls.split(",") if u.strip()
    }

    principal_url = f"https://caldav.yandex.ru/principals/users/{user}/"
    client = caldav.DAVClient(url=principal_url, username=user, password=password)
    principal = client.principal()

    rows: list[dict[str, Any]] = []
    for cal in principal.calendars():
        name = getattr(cal, "name", None) or str(cal)
        url = str(cal.url)

        if configured_urls and url not in configured_urls:
            continue
        if not calendar_included(name, cfg):
            continue

        try:
            events = _search_events(cal, start_dt, end_dt)
        except Exception:
            continue

        for ev in events:
            row = _extract_event_row(ev, name, url)
            if row:
                rows.append(row)

    return rows


def calendar_urls_from_env() -> list[str]:
    return [unquote(u.strip()) for u in settings.yandex_calendar_urls.split(",") if u.strip()]
