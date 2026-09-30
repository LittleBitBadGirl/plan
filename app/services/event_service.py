"""Бизнес-логика раздела «Мероприятия».

Три задачи сервиса:
1. Выборки — что попадает в ближайшую неделю, в месяц, в «будущее» и «прошлое».
   Мероприятие может быть периодом (выставка открыта три недели), поэтому везде
   проверяется пересечение периода с окном, а не только дата начала.
2. Представление — карточка мероприятия собирается здесь (view-словарь), шаблон
   только рисует. Так «до 3 ноя» и «осталось 5 дней» считаются в одном месте.
3. Метаданные по ссылке — Вера вставляет адрес страницы, сервис достаёт название,
   картинку, описание, место и даты (OpenGraph + JSON-LD schema.org/Event).
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import re
import socket
import uuid
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import quote, urljoin, urlparse

import httpx
from sqlalchemy import and_, func, or_, select

from app.models.event import (
    EVENT_STATUSES,
    STATUS_GOING,
    STATUS_NONE,
    STATUS_NOT_GOING,
    Event,
    event_is_active,
)

# --------------------------------------------------------------------------
# подписи и формат дат (русские, без emoji)
# --------------------------------------------------------------------------

STATUS_LABELS = {
    STATUS_GOING: "Иду",
    STATUS_NONE: "Без отметки",
}
# Порядок в форме: сначала решение «иду», потом «без отметки». «Не иду» убрано.
STATUS_ORDER = (STATUS_GOING, STATUS_NONE)

# Календарь похода: московское время (перехода на летнее не было с 2014), событие
# в календаре на два часа и напоминание за сутки — ровно то, что просила Вера.
CALENDAR_TZ = "Europe/Moscow"
CALENDAR_UTC_OFFSET = 3
VISIT_HOURS = 2
REMINDER_HOURS_BEFORE = 24

RU_WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
RU_MONTHS_SHORT = (
    "янв", "фев", "мар", "апр", "мая", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
)
RU_MONTHS_FULL = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)

WEEK_DAYS = 7
UPCOMING_LIMIT = 60
PAST_LIMIT = 20


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n)) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def days_word(n: int) -> str:
    return plural(n, "день", "дня", "дней")


def events_word(n: int) -> str:
    return plural(n, "мероприятие", "мероприятия", "мероприятий")


def short_date(day: date) -> str:
    """«8 окт» — компактно, без года: в ближайшей неделе год лишний."""
    return f"{day.day} {RU_MONTHS_SHORT[day.month - 1]}"


def weekday_label(day: date) -> str:
    return f"{RU_WEEKDAYS[day.weekday()]}, {short_date(day)}"


def month_label(year: int, month: int) -> str:
    return f"{RU_MONTHS_FULL[month - 1]} {year}"


def relative_label(day: date, today: date) -> str:
    """«сегодня», «завтра», «через 3 дня» — для ближайшей недели."""
    diff = (day - today).days
    if diff == 0:
        return "сегодня"
    if diff == 1:
        return "завтра"
    if 2 <= diff <= 6:
        return f"через {diff} {days_word(diff)}"
    return ""


def normalize_status(value: Optional[str]) -> str:
    """Всё, кроме «иду», читается как «без отметки».

    Так же читаются старые строки со статусом «не иду»: из интерфейса решение
    убрано, и карточка не должна показывать его как действующее.
    """
    value = (value or "").strip()
    return value if value in EVENT_STATUSES else STATUS_NONE


def parse_iso_date(raw: Optional[str]) -> Optional[date]:
    """Дата из ISO-строки (с временем и зоной или без)."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def parse_time(raw: Optional[str]) -> Optional[str]:
    """«19:00», «19.00», «19» → «19:00». Мусор → None."""
    text = (raw or "").strip().replace(".", ":")
    if not text:
        return None
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def parse_form_date(raw: Optional[str]) -> Optional[date]:
    """Дата из формы: <input type=date> или ДД.ММ.ГГГГ. Пусто/мусор → None."""
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def normalize_url(raw: Optional[str]) -> Optional[str]:
    """Ссылка мероприятия: только http(s). «afisha.ru/x» → «https://afisha.ru/x»."""
    url = (raw or "").strip()
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url[:1000]
    if re.match(r"^[\w.-]+\.[A-Za-z]{2,}(/\S*)?$", url):
        return ("https://" + url)[:1000]
    return None


def date_label(day: date) -> str:
    """Дата для формы/списка: ДД.ММ.ГГГГ."""
    return day.strftime("%d.%m.%Y")


# --------------------------------------------------------------------------
# представление мероприятия
# --------------------------------------------------------------------------

def event_image(ev: Event) -> str:
    """Своя картинка важнее пришедшей со страницы, но может не быть ни одной."""
    if ev.image_file:
        return f"/uploads/{ev.image_file}"
    return ev.image_url or ""


def event_view(ev: Event, today: date) -> dict:
    """Все, что нужно шаблону: подписи, цвета, состояние диапазона."""
    last_day = ev.last_day
    is_range = ev.is_range
    days_left = (last_day - today).days
    if not is_range:
        when = date_label(ev.start_date)
    elif ev.start_date <= today <= last_day:
        when = f"идёт до {short_date(last_day)}"
    else:
        when = f"{short_date(ev.start_date)} — {short_date(last_day)}"

    if ev.start_date == last_day:
        period_hint = relative_label(ev.start_date, today)
    elif today < ev.start_date:
        period_hint = relative_label(ev.start_date, today)
    elif today == last_day:
        period_hint = "последний день"
    elif days_left > 0:
        period_hint = f"осталось {days_left} {days_word(days_left)}"
    else:
        # Прошедшее мероприятие: «осталось -5 дней» показывать нечего.
        period_hint = ""

    status = normalize_status(ev.status)
    going = status == STATUS_GOING and bool(ev.visit_date)
    return {
        "id": ev.id,
        "title": ev.title,
        "description": ev.description or "",
        "url": ev.url or "",
        "image": event_image(ev),
        "location": ev.location or "",
        "start_date": ev.start_date,
        "end_date": ev.end_date,
        "start_date_iso": ev.start_date.isoformat(),
        "end_date_iso": ev.end_date.isoformat() if ev.end_date else "",
        "start_date_label": date_label(ev.start_date),
        "end_date_label": date_label(ev.end_date) if ev.end_date else "",
        "start_time": ev.start_time or "",
        "status": status,
        "status_label": STATUS_LABELS[status],
        # Поход: «иду 5 окт, 14:00». Показывается только когда бронь действительно
        # поставлена, иначе дашборд рисовал бы «иду» без дня.
        "has_visit": going,
        "visit_date": ev.visit_date,
        "visit_date_iso": ev.visit_date.isoformat() if ev.visit_date else "",
        "visit_time": ev.visit_time or "",
        "visit_label": visit_label(ev) if going else "",
        # День похода отдельной подписью: в блоке недели он важнее даты открытия.
        "visit_day_label": weekday_label(ev.visit_date) if going else "",
        "calendar_url": google_calendar_url(ev) if going else "",
        "calendar_ics": f"/events/{ev.id}/visit.ics" if going else "",
        "is_range": is_range,
        "last_day": last_day,
        "when": when,
        "period_hint": period_hint,
        "day_label": weekday_label(ev.start_date),
        "ends_label": short_date(last_day) if is_range else "",
        "is_past": last_day < today,
        "is_now": ev.start_date <= today <= last_day,
        "in_week": today <= last_day and ev.start_date <= today + timedelta(days=WEEK_DAYS - 1),
    }


def event_views(events: Iterable[Event], today: date) -> list[dict]:
    return [event_view(ev, today) for ev in events]


# --------------------------------------------------------------------------
# поход: «иду» — это бронь, из неё собирается событие календаря
# --------------------------------------------------------------------------

def visit_label(ev: Event) -> str:
    """«5 окт, 14:00» — когда Вера идёт. Без времени — только день."""
    if not ev.visit_date:
        return ""
    label = short_date(ev.visit_date)
    moment = parse_time(ev.visit_time or "")
    return f"{label}, {moment}" if moment else label


def visit_datetime(ev: Event) -> Optional[datetime]:
    """Момент похода: день брони + время.

    Время берём из брони, иначе из времени начала мероприятия, иначе 12:00 —
    так у выставки без времени сеанса всё равно получается осмысленное событие.
    """
    if not ev.visit_date:
        return None
    moment = parse_time(ev.visit_time or "") or parse_time(ev.start_time or "") or "12:00"
    return datetime.combine(ev.visit_date, dtime(int(moment[:2]), int(moment[3:])))


def calendar_window(ev: Event) -> Optional[tuple[datetime, datetime]]:
    """Начало и конец события в календаре: поход на VISIT_HOURS часов."""
    start = visit_datetime(ev)
    if not start:
        return None
    return start, start + timedelta(hours=VISIT_HOURS)


def google_calendar_url(ev: Event) -> str:
    """Ссылка «добавить в Google Календарь»: открывается заполненная форма.

    Это самый быстрый путь — ничего не надо настраивать, в отличие от доступа к
    календарю по API. Напоминание Google при такой вставке не переносит, поэтому
    рядом лежит файл .ics: там напоминание за сутки (VALARM) уже внутри.
    """
    window = calendar_window(ev)
    if not window:
        return ""
    start, finish = window
    description = " ".join(
        part for part in ((ev.description or "").strip(), (ev.url or "").strip()) if part
    )
    fields = {
        "action": "TEMPLATE",
        "text": ev.title or "",
        "dates": f"{start:%Y%m%dT%H%M%S}/{finish:%Y%m%dT%H%M%S}",
        "ctz": CALENDAR_TZ,
        "details": description,
        "location": (ev.location or "").strip(),
    }
    query = "&".join(
        f"{key}={quote(str(value))}" for key, value in fields.items() if value
    )
    return f"https://calendar.google.com/calendar/render?{query}"


def _ics_escape(text: str) -> str:
    """Экранирование по RFC 5545: запятые, точки с запятой, переводы строк."""
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _ics_utc(moment: datetime) -> str:
    """Момент похода в UTC: день и время в записи — московские, смещение +3.

    МСК не переходит на летнее время с 2014 года, поэтому смещение постоянное.
    """
    return (moment - timedelta(hours=CALENDAR_UTC_OFFSET)).strftime("%Y%m%dT%H%M%SZ")


def _ics_fold(line: str, limit: int = 73) -> list[str]:
    """RFC 5545: длинную строку режем по октетам, продолжение — с пробелом впереди.

    Русское описание — это 2 байта на букву, поэтому без переноса строка уходит за
    предел и строгие клиенты обрезают текст события. Пару «обратный слэш + символ»
    (\\n, \\, вместо запятой и точки с запятой) не разрываем: иначе клиент прочитает
    escape-последовательность как обычный текст.
    """
    chunks: list[str] = []
    current = ""
    size = 0
    escaped = False
    for char in line:
        width = len(char.encode("utf-8"))
        if not escaped and size + width > limit and current:
            chunks.append(current)
            current = ""
            size = 0
        current += char
        size += width
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
    chunks.append(current)
    return chunks


def ics_filename(ev: Event) -> str:
    """Имя файла для скачивания: название без символов, запрещённых в именах."""
    stem = re.sub(r'[\\/:*?"<>|]+', " ", (ev.title or "").strip())
    stem = re.sub(r"\s+", " ", stem).strip()[:60]
    return f"{stem or f'мероприятие-{ev.id}'}.ics"


def build_ics(ev: Event, *, now: Optional[datetime] = None) -> str:
    """Файл календаря на поход: событие плюс напоминание за сутки (VALARM)."""
    window = calendar_window(ev)
    if not window:
        return ""
    start, finish = window
    # DTSTAMP — момент создания файла в UTC. Берём настоящее UTC-время, а не
    # локальное: результат не должен зависеть от часового пояса хоста.
    stamp = now or datetime.now(timezone.utc).replace(tzinfo=None)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Planner//Мероприятия//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:event-{ev.id}@planner",
        f"DTSTAMP:{stamp:%Y%m%dT%H%M%SZ}",
        f"DTSTART:{_ics_utc(start)}",
        f"DTEND:{_ics_utc(finish)}",
        f"SUMMARY:{_ics_escape(ev.title or '')}",
    ]
    if ev.location:
        lines.append(f"LOCATION:{_ics_escape(ev.location)}")
    description = " ".join(
        part for part in ((ev.description or "").strip(), (ev.url or "").strip()) if part
    )
    if description:
        lines.append(f"DESCRIPTION:{_ics_escape(description)}")
    if ev.url:
        lines.append(f"URL:{ev.url}")
    lines += [
        "BEGIN:VALARM",
        f"TRIGGER:-PT{REMINDER_HOURS_BEFORE}H",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{_ics_escape(ev.title or '')}",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ]
    return "\r\n".join("\r\n ".join(_ics_fold(line)) for line in lines)


# --------------------------------------------------------------------------
# выборки
# --------------------------------------------------------------------------

def _range_overlaps(start: date, end: date):
    """Условие «мероприятие пересекается с окном [start, end]».

    Пишем через условия на колонки, а не через func.coalesce: у coalesce тип
    вывода неизвестен, и дата уходит в SQLite сырым объектом. Для периодов
    (end_date заполнена) сравниваем конец, для одиночных — саму дату.
    """
    return (
        Event.start_date <= end,
        or_(
            and_(Event.end_date.is_not(None), Event.end_date >= start),
            and_(Event.end_date.is_(None), Event.start_date >= start),
        ),
    )


def _not_finished_before(day: date):
    """Мероприятие ещё не закончилось (для «ближайших» и счётчика)."""
    return or_(
        and_(Event.end_date.is_not(None), Event.end_date >= day),
        and_(Event.end_date.is_(None), Event.start_date >= day),
    )


def _finished_before(day: date):
    """Мероприятие уже закончилось."""
    return or_(
        and_(Event.end_date.is_not(None), Event.end_date < day),
        and_(Event.end_date.is_(None), Event.start_date < day),
    )


def _order():
    # Сначала по дате начала, внутри дня — мероприятия со временем, потом без.
    return (
        Event.start_date.asc(),
        Event.start_time.is_(None).asc(),
        Event.start_time.asc(),
        Event.id.asc(),
    )


async def load_events_between(db, start: date, end: date) -> list[Event]:
    """Все активные мероприятия, которые идут хотя бы один день в окне."""
    result = await db.execute(
        select(Event)
        .where(event_is_active(), *_range_overlaps(start, end))
        .order_by(*_order())
    )
    return list(result.scalars().all())


async def load_week_events(db, today: date, days: int = WEEK_DAYS) -> list[Event]:
    """Ближайшая неделя: начинающиеся и длящиеся (выставка с датой окончания)."""
    return await load_events_between(db, today, today + timedelta(days=days - 1))


async def load_upcoming_events(db, today: date, limit: int = UPCOMING_LIMIT) -> list[Event]:
    """Всё, что ещё не закончилось, — по возрастанию даты."""
    result = await db.execute(
        select(Event)
        .where(event_is_active(), _not_finished_before(today))
        .order_by(*_order())
        .limit(limit)
    )
    return list(result.scalars().all())


async def load_past_events(db, today: date, limit: int = PAST_LIMIT) -> list[Event]:
    """Последние завершившиеся — свежие сверху."""
    result = await db.execute(
        select(Event)
        .where(event_is_active(), _finished_before(today))
        .order_by(Event.start_date.desc(), Event.id.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_active_events(db) -> int:
    result = await db.execute(
        select(func.count(Event.id)).where(event_is_active(), _not_finished_before(date.today()))
    )
    return result.scalar() or 0


async def week_block(db, today: date, limit: int = 6) -> dict:
    """Данные блока «Мероприятия» для дашборда: ближайшие семь дней.

    Живёт в сервисе, а не в роуте, потому что блок нужен в двух местах:
    на дашборде и в ответе на смену статуса.
    """
    week = await load_week_events(db, today)
    items = event_views(week, today)
    return {
        "week_items": items[:limit],
        "week_more": max(0, len(items) - limit),
        "week_count": len(items),
        "ev_month": today.strftime("%Y-%m"),
        "ev_day": today.isoformat(),
        "today": today,
    }


def month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, next_month - timedelta(days=1)


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


# Помощники для шаблона сетки: у каждой ячейки дня — метка по статусу.
def _cell_state(items: list[dict]) -> str:
    statuses = {item["status"] for item in items}
    if STATUS_GOING in statuses:
        return STATUS_GOING
    if STATUS_NONE in statuses:
        return STATUS_NONE
    return STATUS_NOT_GOING


def build_month_grid(
    year: int,
    month: int,
    events: list[Event],
    today: date,
    selected: Optional[date] = None,
) -> dict:
    """Сетка месяца: по каждому дню — список мероприятий и его цвет.

    Периоды раскрашивают все свои дни, поэтому выставка видна в календаре как
    полоса, а не одной точкой в день открытия.
    """
    first, last = month_bounds(year, month)
    by_day: dict[date, list[dict]] = {}
    for ev in events:
        view = event_view(ev, today)
        day = max(ev.start_date, first)
        finish = min(ev.last_day, last)
        while day <= finish:
            by_day.setdefault(day, []).append(
                {
                    **view,
                    "is_first_day": day == ev.start_date,
                    "is_last_day": day == ev.last_day,
                }
            )
            day += timedelta(days=1)

    prev_year, prev_month = shift_month(year, month, -1)
    next_year, next_month = shift_month(year, month, 1)

    days = []
    for num in range(1, last.day + 1):
        day = date(year, month, num)
        items = by_day.get(day, [])
        days.append(
            {
                "date": day,
                "date_iso": day.isoformat(),
                "day_num": num,
                "is_today": day == today,
                "is_selected": day == selected,
                "is_past": day < today,
                "has_events": bool(items),
                "count": len(items),
                "state": _cell_state(items) if items else "",
                # Период рисуется полосой через все свои дни: видно, что
                # выставка открыта неделю, а не один день.
                "has_range": any(item["is_range"] for item in items),
                "range_start": any(item["is_range"] and item["is_first_day"] for item in items),
                "range_end": any(item["is_range"] and item["is_last_day"] for item in items),
                "titles": ", ".join(item["title"] for item in items[:4]),
                "items": items,
            }
        )

    return {
        "year": year,
        "month": month,
        "label": month_label(year, month),
        "prev_query": f"{prev_year}-{prev_month:02d}",
        "next_query": f"{next_year}-{next_month:02d}",
        "start_weekday": first.weekday(),  # 0 = понедельник
        "days": days,
        "weekdays": RU_WEEKDAYS,
        "month_total": sum(1 for day in days if day["has_events"]),
    }


def month_from_query(raw: Optional[str], today: date) -> tuple[int, int]:
    match = re.match(r"^(\d{4})-(\d{1,2})$", (raw or "").strip())
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12 and 2000 <= year <= 2100:
            return year, month
    return today.year, today.month


# --------------------------------------------------------------------------
# метаданные по ссылке
# --------------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
FETCH_TIMEOUT = 12.0
MAX_HTML_BYTES = 400_000

EVENT_LD_TYPES = {
    "event",
    "exhibitionevent",
    "musicevent",
    "theaterevent",
    "festival",
    "businessevent",
    "educationevent",
    "comedyevent",
    "sportsevent",
    "screeningevent",
    "dramaevent",
    "festevent",
    "course",
    "conference",
    "artexhibition",
}

_META_RE = re.compile(r"<meta\s+([^>]+?)/?>", re.IGNORECASE | re.DOTALL)
_ATTR_RE = re.compile(
    r"""([\w:.\-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.DOTALL
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_JSONLD_RE = re.compile(
    r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL
)
_DATE_RE = re.compile(r'"(\w*[Dd]ate)"\s*:\s*"([^"]{4,40})"')


def hosts_allowed(url: str) -> tuple[bool, str]:
    """Пускать только публичный http(s): без localhost и приватных сетей.

    Эндпоинт принимает адрес от пользователя и ходит по нему с сервера, поэтому
    внутренние адреса (роутер, сам VPS, метаданные облака) — запрещены.
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        return False, "Нужна ссылка вида https://..."
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False, "В ссылке нет адреса"
    if host in ("localhost",) or host.endswith(".local"):
        return False, "Локальные адреса не поддерживаются"
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False, "Локальные адреса не поддерживаются"
    except ValueError:
        try:
            resolved = socket.getaddrinfo(host, None)
        except OSError:
            return False, "Не удалось разрешить адрес"
        for entry in resolved:
            try:
                ip = ipaddress.ip_address(entry[4][0])
            except ValueError:
                continue
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False, "Локальные адреса не поддерживаются"
    return True, ""


async def ensure_public_url(url: str) -> tuple[bool, str]:
    """То же, но без блокировки event loop: getaddrinfo — системный вызов."""
    return await asyncio.to_thread(hosts_allowed, url)


IMAGE_URL_PREFIX = "data:image/"


def safe_image_url(value: str) -> Optional[str]:
    """Картинка со страницы или из формы: пускаем только http(s) и data:image/.

    В src тега img недопустимы javascript: и file:, а проверка схемы здесь такая
    же дешёвая, как у самой ссылки мероприятия.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.lower().startswith(IMAGE_URL_PREFIX):
        return raw[:1000]
    parsed = urlparse(raw)
    if parsed.scheme in ("http", "https") and parsed.hostname:
        return raw[:1000]
    return None


def _meta_map(html_text: str) -> dict:
    values: dict[str, str] = {}
    for tag in _META_RE.findall(html_text):
        attrs = {}
        for match in _ATTR_RE.finditer(tag):
            attrs[match.group(1).lower()] = (
                match.group(2) or match.group(3) or match.group(4) or ""
            )
        key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
        content = attrs.get("content") or attrs.get("value") or ""
        if key and content and key not in values:
            values[key] = html.unescape(content).strip()
    return values


def _walk_ld(node, found: dict) -> None:
    """Собираем первое событие из JSON-LD schema.org."""
    if isinstance(node, list):
        for item in node:
            _walk_ld(item, found)
        return
    if not isinstance(node, dict):
        return
    node_type = node.get("@type")
    types = node_type if isinstance(node_type, list) else [node_type]
    names = {str(t).lower() for t in types if t}
    if names & EVENT_LD_TYPES and not found.get("start_date"):
        found["start_date"] = node.get("startDate") or ""
        found["end_date"] = node.get("endDate") or ""
        if not found.get("title"):
            found["title"] = node.get("name") or ""
        location = node.get("location")
        if isinstance(location, dict):
            found["location"] = location.get("name") or ""
        elif isinstance(location, str):
            found["location"] = location
        image = node.get("image")
        if isinstance(image, list) and image:
            image = image[0]
        if isinstance(image, dict):
            image = image.get("url")
        if not found.get("image_url") and isinstance(image, str):
            found["image_url"] = image
    for value in node.values():
        if isinstance(value, (dict, list)):
            _walk_ld(value, found)


def parse_metadata(html_text: str, base_url: str) -> dict:
    """Разбор страницы: OpenGraph, обычные мета-теги, JSON-LD (schema.org/Event)."""
    meta = _meta_map(html_text)
    found: dict = {}
    for raw in _JSONLD_RE.findall(html_text):
        try:
            _walk_ld(json.loads(raw.strip()), found)
        except (ValueError, TypeError):
            continue

    title = (
        meta.get("og:title")
        or meta.get("twitter:title")
        or found.get("title")
        or meta.get("title")
    )
    if not title:
        match = _TITLE_RE.search(html_text)
        if match:
            title = re.sub(r"\s+", " ", match.group(1)).strip()

    image = meta.get("og:image:secure_url") or meta.get("og:image") or meta.get("twitter:image") or found.get("image_url") or ""
    if image:
        image = urljoin(base_url, html.unescape(image).strip())

    description = (
        meta.get("og:description")
        or meta.get("twitter:description")
        or meta.get("description")
        or ""
    )
    description = re.sub(r"\s+", " ", description).strip()[:2000]

    site = meta.get("og:site_name") or urlparse(base_url).hostname or ""

    start_raw = found.get("start_date") or ""
    end_raw = found.get("end_date") or ""
    if not start_raw:
        # Запасной путь: даты в разметке без @type (частый случай у билетных сайтов).
        dates = dict(_DATE_RE.findall(html_text))
        start_raw = dates.get("startDate") or dates.get("StartDate") or ""
        end_raw = dates.get("endDate") or ""
    start_date = parse_iso_date(start_raw)
    end_date = parse_iso_date(end_raw)
    start_time = ""
    if start_raw and "T" in start_raw:
        start_time = (start_raw.split("T", 1)[1][:5] or "").replace(".", ":")

    return {
        "title": html.unescape(title or "").strip()[:500],
        "description": html.unescape(description),
        "image_url": image[:1000],
        "site": site,
        "location": (found.get("location") or "").strip()[:500],
        "start_date": start_date.isoformat() if start_date else "",
        "end_date": end_date.isoformat() if end_date and start_date and end_date != start_date else "",
        "start_time": start_time if re.match(r"^\d{2}:\d{2}$", start_time) else "",
    }


REDIRECT_LIMIT = 5
MAX_BODY_BYTES = 2 * 1024 * 1024


async def _read_html(
    client: httpx.AsyncClient, url: str, result: dict
) -> Optional[tuple[str, str]]:
    """Забрать страницу, проверяя КАЖДЫЙ адрес и не читая тело целиком.

    Редиректы разбираем сами: адрес из заголовка Location тоже должен пройти
    hosts_allowed, иначе страница уведёт запрос на 127.0.0.1 или на
    169.254.169.254, и мы прочитаем внутренний сервис. Тело читаем кусками и
    обрываем на MAX_HTML_BYTES: ссылка на гигабайтный файл не должна съесть
    память сервера.
    """
    current = url
    for _ in range(REDIRECT_LIMIT + 1):
        allowed, reason = await ensure_public_url(current)
        if not allowed:
            result["error"] = reason
            return None
        async with client.stream("GET", current) as response:
            if response.is_redirect:
                location = response.headers.get("location", "")
                if not location:
                    result["error"] = "Страница отвечает переходом без адреса"
                    return None
                current = str(httpx.URL(current).join(location))
                continue

            response.raise_for_status()

            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
                result["error"] = "Страница слишком большая, чтобы её читать"
                return None

            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size >= MAX_HTML_BYTES:
                    break
            body = b"".join(chunks)[:MAX_HTML_BYTES]
            encoding = response.encoding or "utf-8"
            return body.decode(encoding, errors="replace"), str(response.url)

    result["error"] = "Слишком много переходов по ссылке"
    return None


async def fetch_link_metadata(url: str) -> dict:
    """Достать данные мероприятия по ссылке. Ошибка — в поле error, без исключения."""
    result = {
        "url": (url or "").strip(),
        "title": "",
        "description": "",
        "image_url": "",
        "site": "",
        "location": "",
        "start_date": "",
        "end_date": "",
        "start_time": "",
        "error": "",
    }

    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"},
        ) as client:
            fetched = await _read_html(client, result["url"], result)
    except httpx.HTTPStatusError as exc:
        result["error"] = f"Страница ответила {exc.response.status_code}"
        return result
    except (httpx.HTTPError, OSError) as exc:
        result["error"] = f"Страница не открылась: {type(exc).__name__}"
        return result

    if fetched is None:
        return result
    text, final_url = fetched

    parsed = parse_metadata(text, final_url)
    result.update(parsed)
    if not result["title"]:
        result["error"] = "Со страницы не удалось прочитать название — заполни руками"
    return result


# --------------------------------------------------------------------------
# картинки мероприятий (свои файлы)
# --------------------------------------------------------------------------

UPLOAD_SUBDIR = "events"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def uploads_dir() -> Path:
    directory = project_root() / "uploads" / UPLOAD_SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def safe_upload_path(relative: str) -> Optional[Path]:
    """Путь внутри uploads/ — защита от '..' из БД."""
    uploads_root = (project_root() / "uploads").resolve()
    candidate = (uploads_root / (relative or "")).resolve()
    if uploads_root not in candidate.parents:
        return None
    return candidate


def delete_upload(relative: Optional[str]) -> None:
    target = safe_upload_path(relative or "")
    if target and target.is_file():
        try:
            target.unlink()
        except OSError:
            pass


async def save_image(upload) -> Optional[str]:
    """Сохранить свою картинку в uploads/events, вернуть относительный путь."""
    filename = getattr(upload, "filename", "") or ""
    if not filename:
        return None
    suffix = Path(filename).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        return None
    try:
        content = await upload.read()
    except Exception:  # noqa: BLE001 — битый аплоад не должен ронять запись
        return None
    if not content or len(content) > MAX_IMAGE_BYTES:
        return None
    name = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}{suffix}"
    (uploads_dir() / name).write_bytes(content)
    return f"{UPLOAD_SUBDIR}/{name}"
