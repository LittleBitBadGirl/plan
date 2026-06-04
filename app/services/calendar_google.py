"""Чтение личного Google Calendar через secret iCal URL."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import httpx
from icalendar import Calendar
from recurring_ical_events import of as recurring_of

from app.config import settings
from app.services.calendar_filter_service import load_calendar_sync_config


def _to_naive_dt(value: datetime | date) -> datetime:
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, datetime.min.time())
    if getattr(value, "tzinfo", None):
        return value.replace(tzinfo=None)
    return value


def _occurrence_uid(base_uid: str, start: datetime) -> str:
    return f"google:{base_uid}@{start.strftime('%Y%m%dT%H%M%S')}"


def _extract_occurrence_row(
    vevent: Any,
    *,
    calendar_name: str,
    calendar_url: str,
) -> dict[str, Any] | None:
    status = str(vevent.get("STATUS", "")).upper()
    if status == "CANCELLED":
        return None

    title = str(vevent.get("SUMMARY", "(без названия)"))[:500]
    start = _to_naive_dt(vevent.start)
    end = None
    if vevent.end is not None:
        end = _to_naive_dt(vevent.end)

    base_uid = str(vevent.get("UID", "")).strip()
    if not base_uid:
        return None

    dtstart = vevent.get("DTSTART")
    is_all_day = False
    if dtstart is not None:
        params = getattr(dtstart, "params", {}) or {}
        is_all_day = params.get("VALUE") == "DATE"
    if not is_all_day and end and start.date() == end.date():
        if (end - start) >= timedelta(days=1):
            is_all_day = True

    recurrence_id = None
    if vevent.rrules:
        recurrence_id = base_uid
    if vevent.get("RECURRENCE-ID") is not None:
        recurrence_id = base_uid

    location = None
    if vevent.get("LOCATION"):
        location = str(vevent.get("LOCATION"))[:500]

    return {
        "external_uid": _occurrence_uid(base_uid, start),
        "recurrence_id": recurrence_id,
        "calendar_name": calendar_name,
        "calendar_url": calendar_url,
        "title": title,
        "start_at": start,
        "end_at": end,
        "location": location,
        "is_recurring": bool(vevent.rrules),
        "is_all_day": is_all_day,
        "calendar_source": "google",
        "calendar_kind": "personal",
    }


def fetch_google_calendar_events(
    *,
    days_past: int | None = None,
    days_future: int | None = None,
) -> list[dict[str, Any]]:
    """Скачать и развернуть события Google Calendar в окне дат."""
    url = (settings.google_calendar_ical_url or "").strip()
    if not url:
        return []

    cfg = load_calendar_sync_config()
    sync_cfg = cfg.get("sync", {})
    google_cfg = cfg.get("google", {})
    days_past = days_past if days_past is not None else sync_cfg.get("horizon_days_past", 1)
    days_future = days_future if days_future is not None else google_cfg.get(
        "horizon_days_future", sync_cfg.get("horizon_days_future", 14)
    )

    today = date.today()
    window_start = datetime.combine(today - timedelta(days=days_past), datetime.min.time())
    window_end = datetime.combine(today + timedelta(days=days_future), datetime.max.time())

    timeout = google_cfg.get("http_timeout_seconds", 60)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        raw = response.content

    cal = Calendar.from_ical(raw)
    calendar_name = str(cal.get("X-WR-CALNAME", "Google — личный"))[:200]
    calendar_url = url.split("?")[0][:500]

    rows: list[dict[str, Any]] = []
    query = recurring_of(cal, skip_bad_series=True)
    for vevent in query.between(window_start, window_end):
        row = _extract_occurrence_row(
            vevent,
            calendar_name=calendar_name,
            calendar_url=calendar_url,
        )
        if row:
            rows.append(row)
    return rows
