"""Shared web dependencies: templates, filters, helpers."""
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import re
from statistics import mean
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import Request
from markupsafe import Markup, escape
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, or_, and_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import async_session
from app.models.task import Task, task_is_active
from app.models.category import Category
from app.services.day_win_service import day_win_view
from app.services.postpones_service import WORK_CATEGORY_NAMES

# ─── Period tracker helpers ───────────────────────────────────────────────────

_PHASE_LABELS = {
    "period":     "Менструация",
    "follicular": "Фолликулярная",
    "ovulation":  "Овуляция",
    "luteal":     "Лютеиновая",
    "pms":        "ПМС",
}

_MONTH_NAMES = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}


def is_weekend(day: date) -> bool:
    """Суббота и воскресенье (date.weekday(): пн=0 … вс=6)."""
    return day.weekday() >= 5


def work_category_id_subquery():
    """Подзапрос: id категорий, которые считаются рабочими.

    Рабочая категория — сама из списка рабочих либо её родитель (у Веры клиенты
    висят подкатегориями под «Работа», по имени подкатегории их не отловить).
    """
    return select(Category.id).where(
        or_(
            Category.name.in_(WORK_CATEGORY_NAMES),
            Category.parent_id.in_(
                select(Category.id).where(Category.name.in_(WORK_CATEGORY_NAMES))
            ),
        )
    )


def work_task_filter():
    """Условие «задача рабочая»."""
    return Task.category_id.in_(work_category_id_subquery())


def not_work_task_filter():
    """Условие «задача не рабочая»: прячем только рабочие.

    Задачи без категории остаются видимыми — они заведомо не про работу.
    """
    return or_(
        Task.category_id.is_(None),
        Task.category_id.notin_(work_category_id_subquery()),
    )


async def work_category_ids(db: AsyncSession) -> set[int]:
    """id всех рабочих категорий — для фильтрации уже загруженных объектов."""
    result = await db.execute(work_category_id_subquery())
    return {row[0] for row in result.all()}


def weekend_hide_work(request=None) -> bool:
    """Прячем ли рабочие задачи прямо сейчас: выходной и нет cookie «показать».

    Нужно и при отрисовке страницы, и при живых обновлениях (OOB-счётчики,
    перерисовка списка после действия) — иначе в субботу список и числа
    разъезжаются с тем, что Вера видит на экране.
    """
    if request is None:
        return False
    return is_weekend(date.today()) and request.cookies.get("show_work") != "1"


def count_workdays_between(start: date, end: date) -> int:
    if start > end:
        return 0
    n = 0
    d = start
    while d <= end:
        if not is_weekend(d):
            n += 1
        d += timedelta(days=1)
    return n


def _sqlite_completed_not_on_weekend():
    """Будни по ЛОКАЛЬНОМУ дню закрытия (SQLite strftime %w: 0=вс, 6=сб).

    Считалось по UTC-дате таймстампа, поэтому закрытие в 00:30 МСК попадало в
    предыдущий (часто выходной) день и выпадало из карточки темпа — разбор 07.10.2026.
    """
    local_day = func.datetime(Task.completed_at, "localtime")
    return func.strftime("%w", local_day).notin_(["0", "6"])


def _period_phase(day: int, avg_cycle: int, avg_period: int) -> str:
    if day <= avg_period:
        return "period"
    ovulation_day = avg_cycle - 14
    if day <= ovulation_day - 2:
        return "follicular"
    if day <= ovulation_day + 1:
        return "ovulation"
    if day <= avg_cycle - 5:
        return "luteal"
    return "pms"


def _build_month_calendar(today: date, period_map: dict) -> tuple[str, int, list]:
    """Обычный календарь текущего месяца — цифры = числа месяца.
    period_map: {date: (has_pain, is_spotting)} — оба bool."""
    import calendar as _cal

    year, month = today.year, today.month
    month_label = f"{_MONTH_NAMES[month]} {year}"
    start_weekday = date(year, month, 1).weekday()
    days_in_month = _cal.monthrange(year, month)[1]

    calendar_days = []
    for day_num in range(1, days_in_month + 1):
        d = date(year, month, day_num)
        if d in period_map:
            has_pain, is_spotting = period_map[d]
            if has_pain:
                state = "pain"
            elif is_spotting:
                state = "spotting"
            else:
                state = "period"
        elif d > today:
            state = "future"
        else:
            state = "none"
        calendar_days.append({
            "date": d,
            "date_str": d.isoformat(),
            "day_num": day_num,
            "state": state,
            "is_today": d == today,
        })

    return month_label, start_weekday, calendar_days


def _group_period_cycles(sorted_entries):
    """Группирует записи в циклы (разрыв ≤ 2 дня). Возвращает (группы, старты циклов).

    Старт цикла — первый день реального кровотечения (не мазни).
    """
    groups: list[list] = []
    if not sorted_entries:
        return groups, []

    group = [sorted_entries[0]]
    for entry in sorted_entries[1:]:
        if (entry.date - group[-1].date).days <= 2:
            group.append(entry)
        else:
            groups.append(group)
            group = [entry]
    groups.append(group)

    starts = []
    for g in groups:
        period_day = next((e.date for e in g if not e.is_spotting), None)
        if period_day is not None:
            starts.append(period_day)
    return groups, starts


def _build_period_archive(archival_entries) -> Optional[dict]:
    """Архив отметок (старое приложение): считается отдельно, в средние не входит."""
    if not archival_entries:
        return None

    groups, starts = _group_period_cycles(sorted(archival_entries, key=lambda e: e.date))
    lengths = [(starts[i + 1] - starts[i]).days for i in range(len(starts) - 1)]
    real_groups = [g for g in groups if any(not e.is_spotting for e in g)]
    day_counts = [sum(1 for e in g if not e.is_spotting) for g in real_groups]

    cycles = []
    for idx, g in enumerate(real_groups):
        bleed = [e for e in g if not e.is_spotting]
        cycles.append({
            "num": idx + 1,
            "start": bleed[0].date.strftime("%d.%m.%Y"),
            "end": bleed[-1].date.strftime("%d.%m.%Y"),
            "period_days": len(bleed),
            "length": lengths[idx] if idx < len(lengths) else None,
        })

    if not cycles:
        return None

    return {
        "cycles": cycles,
        "count": len(cycles),
        "tracked_days": len(archival_entries),
        "avg_cycle": round(mean(lengths)) if lengths else None,
        "avg_period": max(1, round(mean(day_counts))) if day_counts else None,
        "min": min(lengths) if lengths else None,
        "max": max(lengths) if lengths else None,
        "first": cycles[0]["start"],
        "last": cycles[-1]["end"],
    }


def compute_period_data(entries, today: date) -> dict:
    """
    entries: list of PeriodEntry objects.
    Returns context dict for the dashboard period tracker card.

    Spotting days (is_spotting=True) are tracked but excluded from avg_period.
    Cycle starts from the first non-spotting day in each group.

    Архивные записи (is_archival=True — перенесённые из старого приложения)
    в средние, текущий цикл и календарь не входят: для них считается
    отдельный блок `archive`.
    """
    entries = list(entries)
    archival_entries = [e for e in entries if getattr(e, "is_archival", False)]
    entries = [e for e in entries if not getattr(e, "is_archival", False)]
    archive = _build_period_archive(archival_entries)

    period_map = {e.date: (e.has_pain, e.is_spotting) for e in entries} if entries else {}

    if not entries:
        month_label, start_weekday, calendar_days = _build_month_calendar(today, period_map)
        return {
            "has_data": False,
            "last_period_start": None,
            "current_cycle_day": None,
            "current_phase": None,
            "current_phase_label": "Отметь первый день",
            "avg_cycle": 28,
            "avg_period": 5,
            "cycle_stddev": None,
            "cycle_min": None,
            "cycle_max": None,
            "regularity": "недостаточно данных",
            "month_label": month_label,
            "start_weekday": start_weekday,
            "calendar_days": calendar_days,
            "days_until_next": None,
            "cycles_history": [],
            "pending_spotting_days": 0,
            "pending_spotting_start": None,
            "archive": archive,
        }

    sorted_entries = sorted(entries, key=lambda e: e.date)

    # Group consecutive entries (gap ≤ 2 days) into cycles
    cycles: list[list] = []
    group = [sorted_entries[0]]
    for entry in sorted_entries[1:]:
        if (entry.date - group[-1].date).days <= 2:
            group.append(entry)
        else:
            cycles.append(group)
            group = [entry]
    cycles.append(group)

    # Cycle starts = first NON-spotting day in each group.
    # A group made up ENTIRELY of spotting is NOT a new cycle — it's
    # pre-menstrual spotting, the period hasn't actually started yet.
    cycle_starts = []
    for g in cycles:
        period_day = next((e.date for e in g if not e.is_spotting), None)
        if period_day is not None:
            cycle_starts.append(period_day)

    # Is the most recent group spotting-only? → period not yet started
    pending_spotting = bool(cycles) and all(e.is_spotting for e in cycles[-1])

    cycle_lengths = [
        (cycle_starts[i + 1] - cycle_starts[i]).days
        for i in range(len(cycle_starts) - 1)
    ]

    # avg_period = mean of non-spotting days per REAL cycle (skip spotting-only groups)
    period_day_counts = [
        sum(1 for e in g if not e.is_spotting)
        for g in cycles
        if any(not e.is_spotting for e in g)
    ]
    avg_cycle = round(mean(cycle_lengths)) if cycle_lengths else 28
    avg_period = max(1, round(mean(period_day_counts))) if period_day_counts else 5

    # Variability metrics
    if len(cycle_lengths) >= 2:
        from statistics import stdev
        cycle_stddev = round(stdev(cycle_lengths), 1)
        cycle_min = min(cycle_lengths)
        cycle_max = max(cycle_lengths)
        # Regularity assessment
        if cycle_stddev <= 1.5:
            regularity = "регулярный"
        elif cycle_stddev <= 3.5:
            regularity = "умеренно нерегулярный"
        else:
            regularity = "нерегулярный"
    else:
        cycle_stddev = None
        cycle_min = cycle_lengths[0] if cycle_lengths else None
        cycle_max = cycle_lengths[0] if cycle_lengths else None
        regularity = "недостаточно данных"

    if cycle_starts:
        # Count from the last REAL period start, never from spotting.
        last_start = cycle_starts[-1]
        current_day = (today - last_start).days + 1
        if pending_spotting:
            # Only spotting so far — show PMS colour + explicit label,
            # never "Менструация". The real cycle hasn't started yet.
            current_phase = "pms"
            current_phase_label = "Мазня · ПМС"
        else:
            current_phase = _period_phase(current_day, avg_cycle, avg_period)
            current_phase_label = _PHASE_LABELS.get(current_phase, "—")
        days_until_next = avg_cycle - current_day if cycle_lengths else None
    else:
        # Whole history is spotting only — no real period day on record yet.
        last_start = None
        current_day = None
        current_phase = "pms"
        current_phase_label = "Мазня (цикл не начался)"
        days_until_next = None

    month_label, start_weekday, calendar_days = _build_month_calendar(today, period_map)

    # Only groups with an actual bleed day are cycles. A spotting-only group
    # (pre-menstrual spotting) is NOT a cycle — it's surfaced separately below.
    cycles_history = []
    real_idx = 0
    for grp in cycles:
        period_entries = [e for e in grp if not e.is_spotting]
        if not period_entries:
            continue
        cl = cycle_lengths[real_idx] if real_idx < len(cycle_lengths) else None
        pain_count = sum(1 for e in grp if e.has_pain)
        full_period_days = len(period_entries)
        first_period_date = period_entries[0].date
        last_period_date = period_entries[-1].date
        spotting_before = sum(1 for e in grp if e.is_spotting and e.date < first_period_date)
        spotting_after = sum(1 for e in grp if e.is_spotting and e.date > last_period_date)

        cycles_history.append({
            "num": real_idx + 1,
            # Cycle starts on the first real bleed day, not the first spotting day
            "start": first_period_date.strftime("%d.%m.%Y"),
            "period_start": first_period_date.strftime("%d.%m.%Y"),
            "length": cl,
            "period_days": full_period_days,
            "pain_days": pain_count,
            "spotting_before": spotting_before,
            "spotting_after": spotting_after,
            "total_days": len(grp),
            "is_current": cl is None,
            "deviation": round(cl - avg_cycle, 1) if cl is not None and cycle_lengths else None,
        })
        real_idx += 1

    # Pre-menstrual spotting in progress: current group is spotting-only.
    if pending_spotting:
        last_grp = cycles[-1]
        pending_spotting_days = sum(1 for e in last_grp if e.is_spotting)
        pending_spotting_start = last_grp[0].date.strftime("%d.%m.%Y")
    else:
        pending_spotting_days = 0
        pending_spotting_start = None

    return {
        "has_data": True,
        "last_period_start": last_start,
        "current_cycle_day": current_day,
        "current_phase": current_phase,
        "current_phase_label": current_phase_label,
        "avg_cycle": avg_cycle,
        "avg_period": avg_period,
        "cycle_stddev": cycle_stddev,
        "cycle_min": cycle_min,
        "cycle_max": cycle_max,
        "regularity": regularity,
        "month_label": month_label,
        "start_weekday": start_weekday,
        "calendar_days": calendar_days,
        "days_until_next": days_until_next,
        "cycles_history": cycles_history,
        "pending_spotting_days": pending_spotting_days,
        "pending_spotting_start": pending_spotting_start,
        "archive": archive,
    }


PERIOD_DASHBOARD_WINDOW_DAYS = 120


async def load_period_entries_for_dashboard(
    db: AsyncSession, today: date, *, window_days: int = PERIOD_DASHBOARD_WINDOW_DAYS
) -> list:
    """Period entries для дашборда — только последние window_days.

    Архивные отметки (is_archival) в карточку дашборда не попадают — они живут
    отдельным блоком на /cycle.
    """
    from app.models.period_entry import PeriodEntry

    window_start = today - timedelta(days=window_days)
    result = await db.execute(
        select(PeriodEntry)
        .where(PeriodEntry.date >= window_start)
        # NULL читаем как «не архив»: колонка заводилась позже данных, и сравнение
        # с False выбрасывало такие отметки из карточки, хотя в средние они
        # попадали (там пустое значение читается как false).
        .where(or_(PeriodEntry.is_archival.is_(None), PeriodEntry.is_archival == False))  # noqa: E712
        .order_by(PeriodEntry.date)
    )
    return list(result.scalars().all())


# Шаблоны
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

# Статика: версия = время правки файла. Ссылка вида design.css?v=169… меняется
# после каждой выкатки, поэтому браузер не может показать старый CSS
# (с фиксированным ?v=1 он это делал, и правки выглядели «не приехавшими»).
static_dir = Path(__file__).parent / "static"


def static_v(rel_path: str) -> str:
    try:
        return "?v=" + str(int((static_dir / rel_path).stat().st_mtime))
    except OSError:
        return "?v=1"


templates.env.globals["static_v"] = static_v

_EMOJI_IN_NAME = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F600-\U0001F64F"
    r"\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0"
    r"\U000024C2-\U0001F251\ufe0f\u200d]+",
    flags=re.UNICODE,
)


def _strip_emoji(text: str) -> str:
    if not text:
        return ""
    cleaned = _EMOJI_IN_NAME.sub("", text)
    return re.sub(r"\s+", " ", cleaned).strip()


templates.env.filters["noemoji"] = _strip_emoji


def _today_date() -> date:
    return date.today()


templates.env.globals["today_date"] = _today_date

_URL_IN_TEXT = re.compile(r"((?:https?://|www\.)[^\s<>\"']+)", re.IGNORECASE)
_URL_TRAILING_PUNCT = re.compile(r"[.,;:!?)\]}>»«„“”\"]+$")
_URL_BARE = re.compile(
    r"(?<![\w@/.-])("
    r"(?:[a-z0-9][a-z0-9-]{0,62}\.)+"
    r"(?:ru|com|net|org|io|kz|by|ua|shop|store|online|site|app|me|click|xyz|it|de|fr|es|tr|cn|jp|co|uk)"
    r"(?:/[^\s<>\"'«»]*)?)",
    re.IGNORECASE,
)


def _linkify(text: str) -> Markup:
    """Превратить URL в тексте в кликабельные ссылки."""
    if not text:
        return Markup("")
    parts: list[str] = []
    pos = 0
    for match in _URL_IN_TEXT.finditer(text):
        parts.append(str(escape(text[pos:match.start()])))
        url = match.group(1)
        punct = ""
        trimmed = _URL_TRAILING_PUNCT.search(url)
        if trimmed:
            punct = trimmed.group(0)
            url = url[: trimmed.start()]
        href = url if url.startswith("http") else f"https://{url}"
        parts.append(
            f'<a href="{escape(href)}" target="_blank" rel="noopener noreferrer" '
            f'class="text-amber-400 hover:text-amber-300 hover:underline break-all">{escape(url)}</a>'
        )
        parts.append(str(escape(punct)))
        pos = match.end()
    parts.append(str(escape(text[pos:])))
    return Markup("".join(parts))


templates.env.filters["linkify"] = _linkify


def _first_url(text: Optional[str]) -> str:
    """Первая ссылка в тексте — чтобы покупку можно было открыть по ссылке."""
    if not text:
        return ""
    match = _URL_IN_TEXT.search(text)
    if match:
        url = match.group(1)
    else:
        bare = _URL_BARE.search(text)
        if not bare:
            return ""
        url = bare.group(1)
    trimmed = _URL_TRAILING_PUNCT.search(url)
    if trimmed:
        url = url[: trimmed.start()]
    if not url:
        return ""
    return url if url.lower().startswith("http") else f"https://{url}"


templates.env.filters["first_url"] = _first_url


def _url_host(text: Optional[str]) -> str:
    """Домен первой ссылки — подпись магазина на карточке покупки."""
    url = _first_url(text)
    if not url:
        return ""
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


templates.env.filters["url_host"] = _url_host


def _render_shopping_list(request: Request, items: list) -> str:
    tpl = templates.get_template("partials/shopping_list.html")
    return tpl.render({"request": request, "items": items})


async def get_categories_list():
    """Получить список категорий только для задач, с приоритетом: Работа → Личное → Бренд"""
    from sqlalchemy import case
    order_priority = case(
        (Category.name == "Работа", 1),
        (Category.name == "Личное", 2),
        (Category.name == "Личный бренд", 3),
        else_=4,
    )
    async with async_session() as db:
        result = await db.execute(
            select(Category)
            .where(Category.type == 'task')
            .order_by(order_priority, Category.name)
        )
        return result.scalars().all()


def _today_task_base_filter(today: date) -> list:
    """Корневые задачи, взятые на день (без recurring).

    С 07.10.2026 день собирается руками: в списке дня ровно то, что Вера взяла
    из бэклога (`tasks.planned_for == today`). Раньше здесь стояло
    `due_date == today`, и ночной ролловер сваливал в день весь хвост
    незакрытых задач — отсюда «50 задач каждый день».
    """
    return [
        Task.planned_for == today,
        Task.parent_task_id == None,
        or_(Task.source != "recurring", Task.source == None),
        Task.item_kind == "task",
    ]


def dashboard_task_order_by():
    """Порядок на дашборде: перенесённые/старые наверх, новые — вниз."""
    return (
        func.coalesce(Task.postpones, 0).desc(),
        Task.created_at.asc(),
        Task.sort_order.asc(),
        Task.id.asc(),
    )


async def load_subtasks_map(db: AsyncSession, task_ids: list[int]) -> dict[int, list[Task]]:
    """Все подзадачи родителей (включая выполненные)."""
    if not task_ids:
        return {}
    result = await db.execute(
        select(Task)
        .where(Task.parent_task_id.in_(task_ids))
        .order_by(Task.created_at.asc())
    )
    subtasks_map: dict[int, list[Task]] = defaultdict(list)
    for sub in result.scalars().all():
        subtasks_map[sub.parent_task_id].append(sub)
    return subtasks_map


async def repair_archived_subtasks(db: AsyncSession) -> None:
    """Снять архив с выполненных подзадач (legacy после старого /complete)."""
    from sqlalchemy import update
    await db.execute(
        update(Task)
        .where(
            Task.parent_task_id.isnot(None),
            Task.status == "выполнена",
            Task.is_archived == True,
        )
        .values(is_archived=False)
    )
    await db.flush()


def _completed_at_local_day(completed_at: Optional[datetime]) -> Optional[date]:
    """Локальный календарный день закрытия (completed_at в БД — UTC)."""
    if not completed_at:
        return None
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    return completed_at.astimezone().date()


def _completed_on_day(completed_at: Optional[datetime], day: date) -> bool:
    """Задача закрыта в указанный локальный календарный день."""
    local_day = _completed_at_local_day(completed_at)
    return local_day == day if local_day else False


def _is_actionable_subtask(sub: Task, today: date) -> bool:
    """Подзадача входит в дневную нагрузку баннера «Сегодня N задач».

    Открытые с DL в будущем не считаются (ещё не сегодняшняя работа).
    Закрытые сегодня считаются всегда — в том числе сделанные раньше DL.
    """
    if sub.item_kind != "task":
        return False
    if sub.status == "выполнена":
        return _completed_on_day(sub.completed_at, today)
    return True


def _utc_day_bounds(day: date) -> tuple[datetime, datetime]:
    """Локальный календарный день → UTC bounds для SQL filter."""
    local_start = datetime.combine(day, time.min).astimezone()
    local_end = datetime.combine(day, time.max).astimezone()
    start_utc = local_start.astimezone(timezone.utc)
    end_utc = local_end.astimezone(timezone.utc)
    return start_utc, end_utc


def _local_moment(moment: datetime) -> datetime:
    """Момент из базы → локальное время процесса.

    В базе время без пояса (naive) и означает UTC — так его пишут все пути, кроме
    бота; SQLite-функции вроде `datetime(x, 'localtime')` трактуют naive так же.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone()


def _local_date(moment: datetime) -> date:
    """Момент из базы → локальная календарная дата.

    График раньше резал сутки по UTC (`func.date(completed_at)`), поэтому закрытие
    в 00:30 МСК попадало в предыдущий столбик, а карточки считали по локальным
    суткам. В базе Веры так лежат 36 закрытий — числа не сходились.
    """
    return _local_moment(moment).date()


async def completed_local_moments(db: AsyncSession, start: date, end: date) -> list[datetime]:
    """Закрытия в локальном времени: для часов и недель, тем же определением.

    Часы и «темп по неделям» в блоке «Анализ Гермеса» считались SQL-функциями по
    UTC и включали подзадачи, регулярные и покупки — «утро/вечер» были сдвинуты на
    3 часа, а числа не сходились с карточками (разбор 07.10.2026).
    """
    rows = await db.execute(
        select(Task.completed_at).where(*_completed_tasks_base_filter(start, end))
    )
    return [_local_moment(m) for (m,) in rows.all() if m is not None]


async def completed_counts_by_local_day(
    db: AsyncSession, start: date, end: date
) -> dict[date, int]:
    """Закрыто по локальным дням — тем же определением, что и карточки.

    Единственный источник для графика, рекорда дня и любых «закрыто по дням»:
    корневые задачи, без регулярных, `item_kind='task'`, локальные сутки.
    """
    rows = await db.execute(
        select(Task.completed_at).where(*_completed_tasks_base_filter(start, end))
    )
    counts: dict[date, int] = {}
    for (moment,) in rows.all():
        if moment is None:
            continue
        day = _local_date(moment)
        counts[day] = counts.get(day, 0) + 1
    return counts


async def _load_today_roots_bundle(
    db: AsyncSession, today: date, hide_work: bool = False
) -> tuple[list[Task], list[Task]]:
    """Корневые задачи на сегодня + расширенный список с родителями закрытых подзадач."""
    roots_result = await db.execute(select(Task).where(*_today_roots_filter(today, hide_work)))
    roots_today = list(roots_result.scalars().all())
    seen = {r.id for r in roots_today}
    roots_with_sub = list(roots_today)

    start_utc, end_utc = _utc_day_bounds(today)
    extra_result = await db.execute(
        select(Task.parent_task_id)
        .where(
            Task.parent_task_id.isnot(None),
            Task.status == "выполнена",
            Task.completed_at.isnot(None),
            Task.completed_at >= start_utc,
            Task.completed_at <= end_utc,
        )
        .distinct()
    )
    extra_parent_ids = {pid for (pid,) in extra_result.all() if pid}

    if extra_parent_ids:
        extra_roots_result = await db.execute(
            select(Task).where(Task.id.in_(extra_parent_ids))
        )
        for parent in extra_roots_result.scalars().all():
            if parent.id not in seen:
                roots_with_sub.append(parent)
                seen.add(parent.id)

    return roots_today, roots_with_sub


async def _today_roots_with_sub_completions(
    db: AsyncSession, today: date, hide_work: bool = False
) -> list[Task]:
    """Корневые задачи на сегодня + родители с подзадачами, закрытыми сегодня."""
    _, roots_with_sub = await _load_today_roots_bundle(db, today, hide_work)
    return roots_with_sub


def _today_roots_filter(today: date, hide_work: bool = False) -> list:
    """Корневые задачи на сегодня: открытые или закрытые сегодня."""
    start_utc, end_utc = _utc_day_bounds(today)
    filters = [
        *_today_task_base_filter(today),
        or_(
            task_is_active(),
            and_(
                Task.status == "выполнена",
                Task.completed_at.isnot(None),
                Task.completed_at >= start_utc,
                Task.completed_at <= end_utc,
            ),
        ),
    ]
    # Выходной и Вера не просила показать работу — рабочие задачи не показываем.
    if hide_work:
        filters.append(not_work_task_filter())
    return filters


@dataclass
class DashboardDayStats:
    completed: int
    total: int
    subtask_progress: dict
    ai_warning: Optional[str]
    recurring_today: list
    actionable_completed: int
    actionable_total: int


async def get_dashboard_day_stats(
    db: AsyncSession, today: Optional[date] = None, hide_work: bool = False
) -> DashboardDayStats:
    """Единый проход: standalone + subtask + recurring + banner."""
    if today is None:
        today = date.today()

    roots_today, roots_with_sub = await _load_today_roots_bundle(db, today, hide_work)

    subs_by_parent: dict[int, list[Task]] = defaultdict(list)
    if roots_with_sub:
        root_ids = [r.id for r in roots_with_sub]
        subs_result = await db.execute(
            select(Task).where(Task.parent_task_id.in_(root_ids))
        )
        for sub in subs_result.scalars().all():
            subs_by_parent[sub.parent_task_id].append(sub)

    parents_with_subs = set(subs_by_parent.keys())

    from app.services.recurring_schedule import (
        filter_recurring_templates,
        load_active_recurring_templates,
    )
    from app.services.recurring_completion_service import get_completed_today_keys

    templates = await load_active_recurring_templates(db)
    completed_keys = await get_completed_today_keys(db, today)
    recurring_all = filter_recurring_templates(templates, today)
    recurring_today = filter_recurring_templates(
        templates, today, exclude_completed_keys=completed_keys
    )

    # Выходной: рабочие периодические задачи тоже не показываем и не считаем.
    if hide_work:
        work_ids = await work_category_ids(db)
        recurring_all = [rt for rt in recurring_all if rt.category_id not in work_ids]
        recurring_today = [rt for rt in recurring_today if rt.category_id not in work_ids]

    standalone_total = 0
    standalone_completed = 0
    for root in roots_today:
        if root.id in parents_with_subs:
            continue
        standalone_total += 1
        if root.status == "выполнена" and _completed_on_day(root.completed_at, today):
            standalone_completed += 1

    recurring_completed = sum(
        1 for rt in recurring_all if (rt.title, rt.category_id) in completed_keys
    )
    completed = standalone_completed + recurring_completed
    total = standalone_total + len(recurring_all)

    parent_total = 0
    parent_done = 0
    subtask_total = 0
    subtask_done = 0
    for root in roots_with_sub:
        subs = subs_by_parent.get(root.id, [])
        if not subs:
            continue
        parent_total += 1
        all_done = True
        for sub in subs:
            subtask_total += 1
            if sub.status == "выполнена":
                subtask_done += 1
            else:
                all_done = False
        if all_done:
            parent_done += 1

    subtask_progress = {
        "parent_total": parent_total,
        "parent_done": parent_done,
        "subtask_total": subtask_total,
        "subtask_done": subtask_done,
    }

    actionable_subs: list[Task] = []
    for root in roots_with_sub:
        for sub in subs_by_parent.get(root.id, []):
            if _is_actionable_subtask(sub, today):
                actionable_subs.append(sub)

    sub_completed = sum(1 for s in actionable_subs if s.status == "выполнена")
    actionable_completed = completed + sub_completed
    actionable_total = total + len(actionable_subs)

    ai_warning = await _build_daily_load_warning(
        db, actionable_completed, actionable_total
    )

    return DashboardDayStats(
        completed=completed,
        total=total,
        subtask_progress=subtask_progress,
        ai_warning=ai_warning,
        recurring_today=recurring_today,
        actionable_completed=actionable_completed,
        actionable_total=actionable_total,
    )


async def get_today_progress(db: AsyncSession) -> tuple[int, int]:
    """Прогресс дня для STANDALONE-задач (без подзадач) + регулярные."""
    bundle = await get_dashboard_day_stats(db)
    return bundle.completed, bundle.total


async def get_subtask_today_progress(db: AsyncSession) -> dict:
    """Прогресс задач С подзадачами: родители + подзадачи."""
    bundle = await get_dashboard_day_stats(db)
    return bundle.subtask_progress


async def get_today_stats(db: AsyncSession):
    """Статистика сегодняшнего дня: обычные задачи + регулярные шаблоны на сегодня."""
    bundle = await get_dashboard_day_stats(db)
    return bundle.completed, bundle.total


async def get_today_actionable_stats(db: AsyncSession) -> tuple[int, int]:
    """Реальная дневная нагрузка для баннера: standalone + подзадачи родителей на сегодня."""
    bundle = await get_dashboard_day_stats(db)
    return bundle.actionable_completed, bundle.actionable_total


def today_stats_oob_html(completed: int, total: int) -> str:
    """HTMX OOB: счётчик и полоска прогресса на дашборде."""
    pct = min(int(completed / total * 100), 100) if total > 0 else 0
    return (
        f'<span id="today-stats-counter" hx-swap-oob="true" '
        f'class="font-bold text-sm text-amber-600">{completed}/{total}</span>'
        f'<div id="today-progress-bar" hx-swap-oob="true" '
        f'class="bg-amber-600 h-full transition-all duration-500" '
        f'style="width: {pct}%"></div>'
    )


def today_subtask_stats_oob_html(sp: dict) -> str:
    """HTMX OOB: полоска прогресса подзадач (сегментированная) + лейбл «Сделано X/Y»."""
    if sp["parent_total"] == 0:
        return (
            f'<div id="today-subtask-stats-block" hx-swap-oob="true" class="hidden"></div>'
        )

    # Сегментированная полоска: каждый сегмент = одна подзадача
    segments_html = ""
    if sp["subtask_total"] > 0:
        segs = []
        for i in range(sp["subtask_total"]):
            is_done = i < sp["subtask_done"]
            segs.append(
                f'<div class="flex-1 h-full rounded-sm transition-all duration-500 '
                f'{"bg-amber-600" if is_done else "bg-dark-600"}'
                f'{" mx-px first:ml-0 last:mr-0" if sp["subtask_total"] > 1 else ""}'
                f'"></div>'
            )
        segments_html = "".join(segs)

    return (
        f'<div id="today-subtask-stats-block" hx-swap-oob="true" class="w-full lg:flex-1 lg:max-w-sm">'
        f'<div class="flex justify-between items-center mb-1 px-1">'
        f'<span class="text-[10px] font-bold text-gray-500 uppercase tracking-widest">Подзадачи</span>'
        f'<span id="today-subtask-counter" class="font-bold text-sm text-amber-600">'
        f'{sp["parent_done"]}/{sp["parent_total"]}'
        f'</span>'
        f'</div>'
        f'<div id="today-subtask-bar" class="w-full bg-dark-800 rounded-full h-1.5 lg:h-1 border border-dark-600 overflow-hidden flex">'
        f'{segments_html}'
        f'</div>'
        f'</div>'
    )


def ai_warning_oob_from(warning: Optional[str]) -> str:
    """HTMX OOB: жёлтый баннер нагрузки на дашборде.

    Текст берётся из _build_daily_load_warning — там уже нет хвоста про «обычно
    вы закрываете», Вера его убрала.
    """
    if warning:
        return (
            f'<div id="ai-warning-block" hx-swap-oob="true" '
            f'class="dash-quick-42__card mb-4 p-4 rounded-lg bg-yellow-900/30 border border-yellow-700 animate-pulse">'
            f'<p class="text-yellow-300">{warning}</p></div>'
        )
    return '<div id="ai-warning-block" hx-swap-oob="true" class="hidden"></div>'


async def append_today_stats_oob(content: str, db: AsyncSession, request=None) -> str:
    """Дописать к ответу прежние блоки аналитики: прогресс задач и подзадачи.

    ``request`` нужен, чтобы на выходных числа считались в том же составе, что
    и список на экране (без рабочих задач).
    """
    hide_work = weekend_hide_work(request)
    bundle = await get_dashboard_day_stats(db, hide_work=hide_work)
    # Числа прогресса — из того же bundle: один проход и один и тот же прогресс
    # и в подмене полосы, и в блоке сводки.
    pool = await build_day_pool_context(
        db, request=request, progress=(bundle.completed, bundle.total)
    )
    # Поздравление дня: закрыл последнюю задачу — оно обязано появиться сразу,
    # не дожидаясь перезагрузки страницы (закрытие карточки списка не рисует).
    day_win = day_win_view(await load_today_day_state(db, hide_work))
    return (
        content
        + today_stats_oob_html(bundle.completed, bundle.total)
        + today_subtask_stats_oob_html(bundle.subtask_progress)
        + ai_warning_oob_from(bundle.ai_warning)
        + render_day_pool_block(pool, oob=True)
        + render_day_win_block(day_win, oob=True)
    )


def _completed_tasks_base_filter(start: date, end: date):
    """Корневые задачи, закрытые в интервале дат (без recurring)."""
    range_start_utc, _ = _utc_day_bounds(start)
    _, range_end_utc = _utc_day_bounds(end)
    return (
        Task.status == "выполнена",
        Task.completed_at.isnot(None),
        Task.completed_at >= range_start_utc,
        Task.completed_at <= range_end_utc,
        Task.parent_task_id == None,
        or_(Task.source != "recurring", Task.source == None),
        Task.item_kind == "task",
    )


def rolling_week_windows(today: date) -> tuple[tuple[date, date], tuple[date, date]]:
    """Текущее и предыдущее окно по 7 ПОЛНЫХ дней; сегодня не входит.

    Было `today-7 … today` — 8 календарных дней, и в них попадала неполная
    сегодняшняя дата: в среду окно содержало две среды, а прошлое окно — одну,
    поэтому «Δ неделя» сравнивала неравные отрезки (решение Веры 07.10.2026).
    """
    current_end = today - timedelta(days=1)
    current_start = current_end - timedelta(days=6)
    prev_end = current_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=6)
    return (current_start, current_end), (prev_start, prev_end)


async def count_completed_tasks(
    db: AsyncSession,
    start: date,
    end: date,
    *,
    workdays_only: bool = False,
) -> int:
    filters = list(_completed_tasks_base_filter(start, end))
    if workdays_only:
        filters.append(_sqlite_completed_not_on_weekend())
    result = await db.execute(select(func.count(Task.id)).where(*filters))
    return result.scalar() or 0


async def get_avg_completed_per_day(
    db: AsyncSession, lookback_days: int = 14
) -> tuple[float, int, date, date]:
    """Среднее закрытых за рабочий день по последним ПОЛНЫМ дням.

    Возвращает (среднее, рабочих дней, начало, конец): без числа рабочих дней
    подпись врёт — в 14 календарных днях их 10, а на странице было написано
    «за 14 раб. дней» (разбор 07.10.2026). Сегодняшний неполный день не входит.
    """
    today = date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=lookback_days - 1)

    completed_in_period = await count_completed_tasks(
        db, start, end, workdays_only=True
    )
    workdays = count_workdays_between(start, end)
    if workdays == 0:
        return 0.0, 0, start, end
    return completed_in_period / workdays, workdays, start, end


async def get_productivity_insights(db: AsyncSession) -> dict:
    """Карточки аналитики: одно определение «закрыто», равные окна полных дней.

    Убрано (решение Веры 07.10.2026): «Темп нед.» — метрика сравнивала неделю с
    базой, в которую входила та же неделя (всегда около 100%), и повторяла
    отвергнутую «сколько обычно закрываю»; «Зависли» — статуса «в работе» в базе
    нет вообще, карточка была всегда нулевой. «Рекорд» теперь берётся из того же
    набора, что и карточка недели, а не из графика с другим определением.
    """
    today = date.today()
    end_30 = today - timedelta(days=1)
    start_30 = end_30 - timedelta(days=29)

    avg, avg_workdays, avg_start, avg_end = await get_avg_completed_per_day(db, 14)
    (cur_start, cur_end), (prev_start, prev_end) = rolling_week_windows(today)
    completed_7d = await count_completed_tasks(
        db, cur_start, cur_end, workdays_only=True
    )
    completed_prev_7d = await count_completed_tasks(
        db, prev_start, prev_end, workdays_only=True
    )
    week_delta = completed_7d - completed_prev_7d
    completed_30d = await count_completed_tasks(db, start_30, end_30, workdays_only=True)

    def _period(a: date, b: date) -> str:
        return f"{a.strftime('%d.%m')}–{b.strftime('%d.%m')}"

    if week_delta > 0:
        week_delta_label = f"+{week_delta}"
    elif week_delta < 0:
        week_delta_label = f"−{abs(week_delta)}"
    else:
        week_delta_label = "0"

    return {
        "avg_workday": int(round(avg)) if avg >= 0.5 else None,
        "avg_workdays": avg_workdays,
        "avg_period": _period(avg_start, avg_end),
        "completed_7d": completed_7d,
        "completed_prev_7d": completed_prev_7d,
        "completed_30d": completed_30d,
        "week_delta": week_delta,
        "week_delta_label": week_delta_label,
        "week_period": _period(cur_start, cur_end),
        "week_prev_period": _period(prev_start, prev_end),
        "month_period": _period(start_30, end_30),
    }


async def _build_daily_load_warning(
    db: AsyncSession, completed: int, total: int
) -> Optional[str]:
    """Текст предупреждения о перегрузке по уже посчитанной нагрузке.

    Только факт: сколько задач, сколько готово и сколько осталось. Хвост
    «Обычно вы закрываете ~N в день» убран — Вера сказала, что он считается
    криво и раздражает, так что и запрос средней за две недели здесь больше
    не нужен.
    """
    remaining = max(total - completed, 0)
    if remaining <= 8:
        return None

    return f"Сегодня {total} задач: {completed} готово, {remaining} осталось."


async def build_daily_load_warning(db: AsyncSession) -> Optional[str]:
    """Предупреждение о перегрузке — только реальные задачи на сегодня."""
    bundle = await get_dashboard_day_stats(db)
    return bundle.ai_warning


def _shopping_stats_oob(total: int, archived_count: int) -> str:
    """HTMX OOB: счётчики на странице /shopping (не внутри #shopping-list)."""
    return (
        f'<span id="total-count" hx-swap-oob="true" '
        f'class="font-bold text-white text-lg">{total}</span>'
        f'<span id="archived-count" hx-swap-oob="true" '
        f'class="font-bold text-green-400 text-lg">{archived_count}</span>'
    )


async def _shopping_counts(db: AsyncSession) -> tuple[int, int]:
    from app.models.shopping import ShoppingItem
    from app.services.shopping_service import load_active_shopping

    items = await load_active_shopping(db)
    archived_count_result = await db.execute(
        select(func.count(ShoppingItem.id)).where(ShoppingItem.is_archived == True)
    )
    archived_count = archived_count_result.scalar() or 0
    return len(items), archived_count


async def _shopping_list_response(request: Request, db: AsyncSession):
    from fastapi.responses import HTMLResponse
    from app.services.shopping_service import load_active_shopping

    items = await load_active_shopping(db)
    total, archived_count = await _shopping_counts(db)
    html = _render_shopping_list(request, items) + _shopping_stats_oob(total, archived_count)
    return HTMLResponse(content=html)


async def _shopping_toggle_response(db: AsyncSession):
    """Ответ на «куплено»: OOB-счётчики; строка удаляется через hx-swap=delete."""
    from fastapi.responses import HTMLResponse

    total, archived_count = await _shopping_counts(db)
    return HTMLResponse(content=_shopping_stats_oob(total, archived_count))


# ─── Reading list («Читать») ─────────────────────────────────────────────────

_READING_URL_RE = re.compile(r"https?://\S+")


def _reading_url(title: str):
    """Извлечь первый URL из строки (если есть) — для кликабельных пунктов «Читать»."""
    if not title:
        return None
    m = _READING_URL_RE.search(title)
    return m.group(0).rstrip(".,);]") if m else None


def reading_items_view(items: list) -> list:
    """ShoppingItem(reading) → dict для шаблона: ссылка, категория, формат, теги, прогресс."""
    view = []
    for it in items:
        pages_total = it.pages_total or 0
        pages_read = it.pages_read or 0
        view.append({
            "id": it.id,
            "title": it.title,
            "url": _reading_url(it.title),
            "content": it.content,
            "status": it.reading_status or "want_to_read",
            "pages_total": it.pages_total,
            "pages_read": pages_read,
            "pages_pct": min(100, round(pages_read / pages_total * 100)) if pages_total else 0,
            "is_archived": bool(it.is_archived),
            "category": it.category.name if it.category else "",
            "reading_format": it.reading_format or "",
            "tags": [tag.name for tag in (it.tags or [])],
        })
    return view


async def books_in_progress(db: AsyncSession) -> tuple[list[dict], list[dict]]:
    """Книги «читаю сейчас» и отдельно «на паузе» — для блока на дашборде.

    Вера: «те книги которые я сейчас читаю надо выносить в дашборд». Прогресс
    лежит в самой записи чтения, а пересказы — в retellings: у книги видно,
    сколько по ней уже наговорено вслух и какая мысль была последней.

    Книга на паузе из блока не исчезает: иначе её легко потерять вместе с
    прогрессом, а прогресс Вера просила сохранить.
    """
    from sqlalchemy import select

    from app.models.shopping import ShoppingItem
    from app.services import retelling_service as retellings

    result = await db.execute(
        select(ShoppingItem)
        .where(
            ShoppingItem.item_kind == "reading",
            ShoppingItem.is_archived == False,  # noqa: E712
            ShoppingItem.reading_status.in_(("reading", "paused")),
        )
        .order_by(ShoppingItem.id.desc())
    )
    items = list(result.scalars().all())
    if not items:
        return [], []

    book_ids = [item.id for item in items]
    stats = await retellings.book_retelling_stats(db, book_ids)
    latest = await retellings.last_thoughts(db, book_ids)
    today = date.today()

    now: list[dict] = []
    paused: list[dict] = []
    for card, item in zip(reading_items_view(items), items):
        card["retellings"] = stats.get(item.id, {"count": 0, "thoughts": 0, "last_day": ""})
        card["thought"] = (latest.get(item.id) or {}).get("thought", "")
        if (item.reading_status or "") == "paused":
            paused_at = item.reading_paused_at
            card["paused_days"] = (today - paused_at.date()).days if paused_at else None
            paused.append(card)
        else:
            now.append(card)
    return now, paused


async def render_reading_now_block(request: Request, db: AsyncSession) -> str:
    """Блок «Читаю сейчас» на дашборде — ответ на кнопку «не читаю»."""
    now, _paused = await books_in_progress(db)
    tpl = templates.get_template("partials/reading_now_block.html")
    return tpl.render({"request": request, "reading_now": now})


async def render_reading_top_block(request: Request, db: AsyncSession, oob: bool = False) -> str:
    """Большой блок «Я читаю сейчас» вверху страницы «Читать».

    Показывает и то, что читается, и то, что отложено: Вера просила, чтобы
    «прям видно было, что я тормознула». ``oob=True`` — блок приезжает ответом на
    кнопку и подменяет себя на месте (элемент помечен hx-swap-oob).
    """
    now, paused = await books_in_progress(db)
    tpl = templates.get_template("partials/reading_top_block.html")
    return tpl.render(
        {"request": request, "reading_now": now, "reading_paused": paused, "oob": oob}
    )


async def reading_filters_from_request(request: Request):
    """Фильтры страницы чтения: из строки запроса, а для POST — из полей формы.

    htmx отправляет фильтры строкой запроса при GET и телом формы при POST
    (кнопки внутри списка подтягивают форму фильтров через hx-include),
    поэтому источник зависит от метода.
    """
    from app.services.reading_service import parse_filters

    keys = ("cat", "fmt", "tag", "status", "q", "all_tags")
    if any(key in request.query_params for key in keys):
        return parse_filters(request.query_params)
    if request.method in ("POST", "PUT", "PATCH"):
        return parse_filters(await request.form())
    return parse_filters(request.query_params)


async def build_reading_context(db: AsyncSession, filters) -> dict:
    """Всё, что нужно странице чтения: полки, счётчики, чипсы фильтров."""
    from app.services import reading_service as rs

    items = await rs.load_reading(db, filters)
    found = await rs.count_reading(db, filters)
    categories = await rs.reading_categories(db)
    # Ключ называется cards, а не items: у словаря есть метод items(), и в Jinja
    # shelf.items вернул бы метод, а не список карточек.
    shelves = [
        {
            "name": shelf["name"],
            "show_only": shelf["show_only"],
            "cards": reading_items_view(shelf["items"]),
        }
        for shelf in rs.group_by_shelf(items, categories, filters)
    ]
    tag_pairs = await rs.tag_counts(db, filters)
    tag_all = len(tag_pairs) <= rs.TAG_CHIPS_LIMIT or filters.all_tags
    # Большой блок «Я читаю сейчас» вверху страницы: что читаю и что отложено.
    reading_now, reading_paused = await books_in_progress(db)
    return {
        "shelves": shelves,
        "reading_now": reading_now,
        "reading_paused": reading_paused,
        # found — сколько всего подходит под фильтры, shown — сколько уже
        # нарисовали (порция PAGE_SIZE, дальше кнопка «показать ещё»).
        "found": found,
        "shown": len(items),
        "page_step": rs.PAGE_STEP,
        "next_limit": min(filters.limit + rs.PAGE_STEP, rs.PAGE_SIZE_MAX),
        "total": await rs.total_reading(db, include_archived=True),
        "categories": [category.name for category in categories],
        "category_counts": await rs.category_counts(db, filters),
        "formats": rs.READING_FORMATS,
        "format_counts": await rs.format_counts(db, filters),
        "tag_pairs": tag_pairs,
        # Теги отдаём все: на широком экране срезать нечего — чипсы влезают целиком,
        # а на узком лишние прячет CSS (класс reading-chip--extra), кнопка
        # «ещё N тегов» остаётся мобильным управлением.
        "tag_chips": tag_pairs,
        "tag_chips_limit": rs.TAG_CHIPS_LIMIT,
        "tag_all": tag_all,
        "filters": filters,
        # Нужны ли out-of-band обновления: список приходит ответом на смену
        # фильтров, а чипсы и счётчик живут вне него.
        "oob": False,
    }


def _render_reading_list(request: Request, context: dict) -> str:
    tpl = templates.get_template("partials/reading_list.html")
    return tpl.render({"request": request, **context})


async def _reading_list_response(request: Request, db: AsyncSession):
    from fastapi.responses import HTMLResponse

    filters = await reading_filters_from_request(request)
    context = await build_reading_context(db, filters)
    # htmx присылает HX-Request: тогда вместе со списком обновляем чипсы фильтров.
    context["oob"] = request.headers.get("HX-Request") == "true"
    content = _render_reading_list(request, context)
    if context["oob"]:
        # Блок «Я читаю сейчас» стоит выше списка и в список не входит: после
        # любой правки (пауза, страницы, статус) его тоже надо перерисовать.
        content += await render_reading_top_block(request, db, oob=True)
    return HTMLResponse(content=content)


def _shift_months(day: date, months: int) -> date:
    """Первое число месяца со сдвигом на months (без dateutil)."""
    total = day.month - 1 + months
    return date(day.year + total // 12, total % 12 + 1, 1)


def _created_base_filter(start: date, end: date):
    """«Прилетело» в интервале дат: новые корневые задачи без регулярных."""
    range_start_utc, _ = _utc_day_bounds(start)
    _, range_end_utc = _utc_day_bounds(end)
    return (
        Task.created_at.isnot(None),
        Task.created_at >= range_start_utc,
        Task.created_at <= range_end_utc,
        Task.parent_task_id == None,
        or_(Task.source != "recurring", Task.source == None),
        Task.item_kind == "task",
    )


async def created_flow(db: AsyncSession, start: date, end: date) -> tuple[dict[date, int], int]:
    """Прилетело по локальным дням + сколько из прилетевшего закрыто в тот же день.

    Второе число сначала считалось через `planned_for`, но так оно врёт в меньшую
    сторону: ночной возврат в бэклог стирает `planned_for` у незакрытых, поэтому в
    прошлых днях остаются только закрытые. По `completed_at` видно прямо: прилетело
    и в тот же день доведено до конца.
    """
    rows = await db.execute(
        select(Task.created_at, Task.completed_at).where(*_created_base_filter(start, end))
    )
    counts: dict[date, int] = {}
    same_day = 0
    for moment, completed in rows.all():
        if moment is None:
            continue
        day = _local_date(moment)
        counts[day] = counts.get(day, 0) + 1
        if completed is not None and _local_date(completed) == day:
            same_day += 1
    return counts, same_day


def _year_buckets_start(buckets: list[dict], fallback: date) -> date:
    """Начало показанного года — первый непустой месяц (иначе начало окна)."""
    if not buckets:
        return fallback
    label = buckets[0]["label"]
    try:
        month, year = label.split(".")
        return date(2000 + int(year), int(month), 1)
    except (ValueError, TypeError):
        return fallback


async def get_flow_data(db: AsyncSession, period: str = "month") -> dict:
    """Поток задач за период: прилетело, закрыто, взято — по дням, неделям, дням недели.

    Вера: «сделай подсчёт, сколько добавляется в день (кроме 5) — 5 я утром выбираю,
    а сегодня ещё 6 добавила, и не из бэклога, а прилетающих задач». Поэтому прилёт и
    закрытия считаются ОДНИМ окном из полных дней и одним определением (корневые без
    регулярных, локальные сутки), а баланс показывается на одной и той же основе.
    """
    today = date.today()
    if period == "week":
        end = today - timedelta(days=1)
        start = end - timedelta(days=6)
    elif period == "year":
        last_full = today.replace(day=1) - timedelta(days=1)
        start, end = _shift_months(last_full, -11), last_full
    else:
        end = today - timedelta(days=1)
        start = end - timedelta(days=29)

    created, same_day = await created_flow(db, start, end)
    closed = await completed_counts_by_local_day(db, start, end)
    # Дни без хвоста — для подсветки на графике: видно не только «сколько
    # закрыто», но и где день был закрыт целиком.
    from app.services.day_win_service import LEVEL_FULL, load_day_wins

    wins = await load_day_wins(db, start, end)

    days = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)

    max_val = max([created.get(d, 0) for d in days] + [closed.get(d, 0) for d in days] + [1])
    series = [
        {
            "day": d,
            "iso": d.isoformat(),
            "short": f"{d.day:02d}.{d.month:02d}",
            "created": created.get(d, 0),
            "closed": closed.get(d, 0),
            "weekend": is_weekend(d),
            "win": d in wins,
            "win_full": d in wins and wins[d].level == LEVEL_FULL,
            "created_h": round(created.get(d, 0) / max_val * 100),
            "closed_h": round(closed.get(d, 0) / max_val * 100),
        }
        for d in days
    ]

    created_total = sum(created.values())
    closed_total = sum(closed.values())
    workdays = count_workdays_between(start, end) or 1
    created_workdays = sum(n for d, n in created.items() if not is_weekend(d))
    closed_workdays = sum(n for d, n in closed.items() if not is_weekend(d))

    dow_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    by_dow = [{"name": name, "created": 0, "closed": 0} for name in dow_names]
    for d in days:
        idx = d.weekday()
        by_dow[idx]["created"] += created.get(d, 0)
        by_dow[idx]["closed"] += closed.get(d, 0)

    buckets: list[dict] = []
    if period == "year":
        cursor = start
        while cursor <= end:
            key = cursor.strftime("%Y-%m")
            covered = sum(1 for d in days if d.strftime("%Y-%m") == key)
            month_days = (_shift_months(cursor, 1) - cursor).days
            buckets.append(
                {
                    "label": cursor.strftime("%m.%y"),
                    "created": sum(n for d, n in created.items() if d.strftime("%Y-%m") == key),
                    "closed": sum(n for d, n in closed.items() if d.strftime("%Y-%m") == key),
                    "days": covered,
                    "partial": covered < month_days,
                }
            )
            cursor = _shift_months(cursor, 1)
    else:
        for d in days:
            iso = d.isocalendar()
            label = f"нед. {iso[1]:02d}"
            if not buckets or buckets[-1]["label"] != label:
                # ISO-неделя на краю окна бывает неполной: 30 дней не делятся на 7,
                # поэтому у крайних недель стоит число дней — иначе полоска недели
                # из одного дня сравнивается с полной неделей.
                buckets.append({"label": label, "created": 0, "closed": 0, "days": 0})
            buckets[-1]["created"] += created.get(d, 0)
            buckets[-1]["closed"] += closed.get(d, 0)
            buckets[-1]["days"] += 1
        for row in buckets:
            row["partial"] = row["days"] != 7
    # MAJOR критика 07.10.2026: обе полоски строки обязаны жить на ОДНОЙ шкале.
    # Раньше каждая нормировалась на свой максимум, и «прилетело 24 при максимуме 55»
    # выходило короче «закрыто 33 при максимуме 50» — сравнение врало.
    if period == "year":
        # Полгода пустых месяцев съедали экран (задачи Веры начинаются с апреля 2026):
        # ведущие месяцы без единого прилёта и закрытия не показываем. Если данных нет
        # вовсе — оставляем полный год, чтобы блок не остался без периода.
        first_with_data = next(
            (i for i, b in enumerate(buckets) if b["created"] or b["closed"]), None
        )
        if first_with_data:
            buckets = buckets[first_with_data:]

    bucket_max = max(
        [b["created"] for b in buckets] + [b["closed"] for b in buckets] + [1]
    )
    for row in buckets:
        row["created_h"] = round(row["created"] / bucket_max * 100)
        row["closed_h"] = round(row["closed"] / bucket_max * 100)

    return {
        "period": period,
        "period_label": f"{start.strftime('%d.%m')}–{end.strftime('%d.%m')}"
        if period != "year"
        else f"{_year_buckets_start(buckets, start).strftime('%m.%Y')}–{end.strftime('%m.%Y')}",
        "series": series,
        "has_bars": any(s["created"] or s["closed"] for s in series),
        "created_total": created_total,
        "closed_total": closed_total,
        "same_day_closed": same_day,
        "weekend_created": sum(n for d, n in created.items() if is_weekend(d)),
        "weekend_closed": sum(n for d, n in closed.items() if is_weekend(d)),
        "created_workdays": created_workdays,
        "closed_workdays": closed_workdays,
        "workdays": workdays,
        "created_per_workday": round(created_workdays / workdays, 1),
        "closed_per_workday": round(closed_workdays / workdays, 1),
        "balance": closed_total - created_total,
        "by_dow": by_dow,
        "buckets": buckets,
        "bucket_word": "месяц" if period == "year" else "неделя",
    }


async def get_tasks_today(db: AsyncSession, request: Request):
    """Вспомогательная функция для получения списка задач на сегодня и их отрисовки"""
    today = date.today()
    weekend = is_weekend(today)

    filters = [
        Task.planned_for == today,
        task_is_active(),
        Task.status.in_(["новая", "в_работе"]),
        Task.parent_task_id == None,
        Task.source.is_distinct_from("recurring"),
        Task.item_kind == "task",
    ]
    if weekend:
        # В выходной ритуала нет: показываем весь личный хвост из бэклога,
        # брать задачи руками не нужно (решение Веры 07.10.2026).
        filters[0] = or_(Task.planned_for == today, Task.planned_for.is_(None))
    if weekend_hide_work(request):
        filters.append(not_work_task_filter())

    result = await db.execute(
        select(Task)
        .options(selectinload(Task.category).selectinload(Category.parent))
        .where(*filters)
        .order_by(*dashboard_task_order_by())
    )
    tasks = result.scalars().all()

    await repair_archived_subtasks(db)
    task_ids = [t.id for t in tasks]
    subtasks_map = await load_subtasks_map(db, task_ids)
    await db.commit()

    # Разделяем: с подзадачами и standalone
    tasks_with_subtasks = [t for t in tasks if subtasks_map.get(t.id)]
    standalone_tasks = [t for t in tasks if not subtasks_map.get(t.id)]

    template = templates.get_template("partials/tasks_list_split.html")
    taken_subs = await get_day_taken_subtasks(db, today)
    # Состояние дня уходит в шаблон: по нему список отличает «день был и всё
    # закрыто» от «дня ещё не было» (см. partials/tasks_list_split.html).
    day_win = day_win_view(
        await load_today_day_state(db, weekend_hide_work(request))
    )
    content = template.render({
        "request": request,
        "tasks_with_subtasks": tasks_with_subtasks,
        "standalone_tasks": standalone_tasks,
        "subtasks_map": subtasks_map,
        "taken_subtasks": taken_subs,
        "day_win": day_win,
    })

    return await append_today_stats_oob(content, db, request)


async def get_day_taken_subtasks(db: AsyncSession, today: Optional[date] = None) -> list[dict]:
    """Подзадачи, взятые на день, вместе с родителем — для подписи «из задачи».

    Родитель в день не попадает: Вера берёт куски, а большая задача ждёт в
    бэклоге и закрывается сама, когда закроются все её подзадачи.
    """
    today = today or date.today()
    result = await db.execute(
        select(Task)
        .options(selectinload(Task.category))
        .where(
            Task.planned_for == today,
            Task.parent_task_id.isnot(None),
            Task.item_kind == "task",
            Task.status != "выполнена",
        )
        .order_by(Task.id.asc())
    )
    subs = list(result.scalars().all())
    if not subs:
        return []

    parent_ids = {s.parent_task_id for s in subs}
    parents_result = await db.execute(select(Task).where(Task.id.in_(parent_ids)))
    parents = {p.id: p for p in parents_result.scalars().all()}

    subs_map = await load_subtasks_map(db, list(parent_ids))
    rows = []
    for sub in subs:
        parent = parents.get(sub.parent_task_id)
        siblings = subs_map.get(sub.parent_task_id, [])
        done = len([s for s in siblings if s.status == "выполнена"])
        rows.append({
            "sub": sub,
            "parent": parent,
            "done": done,
            "total": len(siblings),
        })
    return rows


async def build_day_pool_context(
    db: AsyncSession,
    request=None,
    today: Optional[date] = None,
    progress: Optional[tuple[int, int]] = None,
) -> dict:
    """Числа и состояние для блока «день»: бэклог, взято, регулярные, ритуал.

    Выходной день живёт без ритуала: блок говорит «весь личный бэклог на экране»
    и не считает взятое.
    """
    from app.services.day_pool_service import (
        MIN_DAY_TASKS,
        count_backlog,
        count_taken,
        counter_label,
    )

    today = today or date.today()
    weekend = is_weekend(today)
    # На самой странице бэклога сводка идёт без числа бэклога и без кнопки
    # «Бэклог»: число дублирует вкладку «Задачи N», а ссылка ведёт на ту же
    # страницу, то есть ничего не делает.
    path = request.url.path if request is not None else ""
    compact = path.startswith("/backlog")

    # Прогресс дня. Числа приходят параметром от того, у кого уже есть bundle
    # (дашборд и OOB-ответ): считать его здесь второй раз нельзя — тест
    # test_append_today_stats_oob_uses_single_bundle держит ровно один проход.
    # Если ничего не передали (страница бэклога — там прогресс дня не показан,
    # стоит прогресс ритуала), считаем дешёвым запросом по взятым задачам.
    if progress is None:
        day_scope = [Task.planned_for == today, Task.item_kind == "task"]
        progress_total = (
            await db.execute(select(func.count(Task.id)).where(*day_scope))
        ).scalar() or 0
        progress_done = (
            await db.execute(
                select(func.count(Task.id)).where(*day_scope, Task.status == "выполнена")
            )
        ).scalar() or 0
        progress = (progress_done, progress_total)
    progress_done, progress_total = progress

    taken = await count_taken(db, today)
    backlog_count = await count_backlog(db)
    recurring_result = await db.execute(
        select(func.count(Task.id)).where(
            Task.source == "recurring",
            Task.due_date == today,
            task_is_active(),
            Task.status.in_(["новая", "в_работе"]),
        )
    )
    recurring_count = recurring_result.scalar() or 0

    return {
        "weekend": weekend,
        "compact": compact,
        "taken": taken,
        "backlog_count": backlog_count,
        "recurring_count": recurring_count,
        "counter": counter_label(taken),
        "progress_done": progress_done,
        "progress_total": progress_total,
        "min_day_tasks": MIN_DAY_TASKS,
        "left_to_pick": max(MIN_DAY_TASKS - taken, 0),
        "show_ritual": (not weekend) and taken < MIN_DAY_TASKS,
    }


def render_day_pool_block(ctx: dict, oob: bool = False) -> str:
    """Отрисовать блок сводки дня (страница и OOB-ответ — один и тот же шаблон)."""
    return templates.get_template("partials/day_pool_block.html").render({
        "pool": ctx,
        "oob": oob,
    })


async def load_today_day_state(db: AsyncSession, hide_work: bool = False):
    """Состояние сегодняшнего дня: закрыто ли всё, что взято на день.

    Определение то же, что у списка дня (см. day_win_service), иначе список и
    поздравление говорили бы про разные дни.
    """
    from app.services.day_win_service import load_day_state

    work_ids = await work_category_ids(db) if hide_work else None
    return await load_day_state(db, date.today(), work_ids)


def render_day_win_block(win: dict, oob: bool = False) -> str:
    """Отрисовать поздравление «день без хвоста» (страница и OOB — один шаблон)."""
    return templates.get_template("partials/day_win_block.html").render({
        "win": win,
        "oob": oob,
    })


__all__ = [
    "templates",
    "compute_period_data",
    "load_period_entries_for_dashboard",
    "PERIOD_DASHBOARD_WINDOW_DAYS",
    "get_categories_list",
    "get_dashboard_day_stats",
    "DashboardDayStats",
    "get_today_stats",
    "get_today_progress",
    "get_today_actionable_stats",
    "get_subtask_today_progress",
    "today_stats_oob_html",
    "today_subtask_stats_oob_html",
    "ai_warning_oob_from",
    "append_today_stats_oob",
    "load_subtasks_map",
    "repair_archived_subtasks",
    "build_daily_load_warning",
    "get_avg_completed_per_day",
    "get_productivity_insights",
    "get_tasks_today",
    "day_win_view",
    "load_today_day_state",
    "render_day_win_block",
    "dashboard_task_order_by",
    "_strip_emoji",
    "_render_shopping_list",
    "_shopping_stats_oob",
    "_shopping_list_response",
    "_shopping_toggle_response",
    "_shopping_counts",
    "reading_items_view",
    "reading_filters_from_request",
    "build_reading_context",
    "_render_reading_list",
    "_reading_list_response",
]
