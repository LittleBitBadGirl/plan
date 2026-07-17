"""Shared web dependencies: templates, filters, helpers."""
from collections import defaultdict
from pathlib import Path
import re
from statistics import mean
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import Request
from markupsafe import Markup, escape
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, or_, and_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import async_session
from app.models.task import Task
from app.models.category import Category

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
    """SQLite strftime %w: 0=вс, 6=сб."""
    w = func.strftime("%w", Task.completed_at)
    return w.notin_(["0", "6"])


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


def compute_period_data(entries, today: date) -> dict:
    """
    entries: list of PeriodEntry objects.
    Returns context dict for the dashboard period tracker card.

    Spotting days (is_spotting=True) are tracked but excluded from avg_period.
    Cycle starts from the first non-spotting day in each group.
    """
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
    }

# Шаблоны
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))
templates.env.cache = None  # Отключаем кэш, чтобы избежать ошибок с хэшированием словарей

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

_URL_IN_TEXT = re.compile(r"((?:https?://|www\.)[^\s<>\"']+)")


def _linkify(text: str) -> Markup:
    """Превратить URL в тексте в кликабельные ссылки."""
    if not text:
        return Markup("")
    parts: list[str] = []
    pos = 0
    for match in _URL_IN_TEXT.finditer(text):
        parts.append(str(escape(text[pos:match.start()])))
        url = match.group(1)
        href = url if url.startswith("http") else f"https://{url}"
        parts.append(
            f'<a href="{escape(href)}" target="_blank" rel="noopener noreferrer" '
            f'class="text-amber-400 hover:text-amber-300 hover:underline break-all">{escape(url)}</a>'
        )
        pos = match.end()
    parts.append(str(escape(text[pos:])))
    return Markup("".join(parts))


templates.env.filters["linkify"] = _linkify


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
    """Корневые задачи на дату (без recurring), единый контракт для дашборда."""
    return [
        Task.due_date == today,
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


def _completed_on_day(completed_at: Optional[datetime], day: date) -> bool:
    """Задача закрыта в указанный календарный день (UTC, как completed_at в БД)."""
    if not completed_at:
        return False
    return completed_at.date() == day


def _today_roots_filter(today: date) -> list:
    """Корневые задачи на сегодня: открытые или закрытые сегодня."""
    return [
        *_today_task_base_filter(today),
        or_(
            Task.is_archived == False,
            and_(
                Task.status == "выполнена",
                Task.completed_at.isnot(None),
                func.date(Task.completed_at) == today.isoformat(),
            ),
        ),
    ]


async def get_today_progress(db: AsyncSession) -> tuple[int, int]:
    """Прогресс дня для STANDALONE-задач (без подзадач) + регулярные.

    Задачи с подзадачами исключены — их прогресс считается отдельно
    через get_subtask_today_progress().
    """
    today = date.today()

    roots_result = await db.execute(select(Task).where(*_today_roots_filter(today)))
    roots = roots_result.scalars().all()

    # Собираем подзадачи для всех корневых, чтобы отфильтровать родителей
    standalone_total = 0
    standalone_completed = 0

    if roots:
        root_ids = [r.id for r in roots]
        subs_result = await db.execute(
            select(Task).where(Task.parent_task_id.in_(root_ids))
        )
        parents_with_subs: set[int] = set()
        for sub in subs_result.scalars().all():
            parents_with_subs.add(sub.parent_task_id)

        for root in roots:
            if root.id in parents_with_subs:
                # Пропускаем: задача с подзадачами — в отдельной полоске
                continue
            standalone_total += 1
            if root.status == "выполнена" and _completed_on_day(root.completed_at, today):
                standalone_completed += 1

    from app.services.recurring_schedule import get_recurring_templates_for_date
    from app.services.recurring_completion_service import get_completed_today_keys

    recurring_today = await get_recurring_templates_for_date(
        db, today, exclude_completed=False
    )
    completed_keys = await get_completed_today_keys(db, today)
    recurring_completed = sum(
        1 for rt in recurring_today if (rt.title, rt.category_id) in completed_keys
    )

    return standalone_completed + recurring_completed, standalone_total + len(recurring_today)


async def get_subtask_today_progress(db: AsyncSession) -> dict:
    """Прогресс задач С подзадачами: родители + подзадачи.

    Returns:
        parent_total: сколько родительских задач с подзадачами на сегодня
        parent_done:  сколько родителей, у которых ВСЕ подзадачи выполнены
        subtask_total: общее количество подзадач
        subtask_done:  сколько подзадач выполнено
    """
    today = date.today()

    roots_result = await db.execute(select(Task).where(*_today_roots_filter(today)))
    roots = roots_result.scalars().all()

    # Also include parents whose subtasks were completed today (future due_date)
    extra_result = await db.execute(
        select(Task)
        .where(
            Task.parent_task_id.isnot(None),
            Task.status == "выполнена",
            Task.completed_at.isnot(None),
            func.date(Task.completed_at) == today.isoformat(),
        )
    )
    extra_subs = extra_result.scalars().all()
    extra_parent_ids = set()
    for sub in extra_subs:
        if sub.parent_task_id:
            extra_parent_ids.add(sub.parent_task_id)
    
    if extra_parent_ids:
        extra_roots_result = await db.execute(
            select(Task).where(Task.id.in_(extra_parent_ids))
        )
        for r in extra_roots_result.scalars().all():
            if r.id not in {p.id for p in roots}:
                roots.append(r)

    if not roots:
        return {"parent_total": 0, "parent_done": 0, "subtask_total": 0, "subtask_done": 0}

    root_ids = [r.id for r in roots]
    subs_result = await db.execute(
        select(Task).where(Task.parent_task_id.in_(root_ids))
    )
    subs_by_parent: dict[int, list[Task]] = defaultdict(list)
    for sub in subs_result.scalars().all():
        subs_by_parent[sub.parent_task_id].append(sub)

    parent_total = 0
    parent_done = 0
    subtask_total = 0
    subtask_done = 0

    for root in roots:
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

    return {
        "parent_total": parent_total,
        "parent_done": parent_done,
        "subtask_total": subtask_total,
        "subtask_done": subtask_done,
    }


async def get_today_stats(db: AsyncSession):
    """Статистика сегодняшнего дня: обычные задачи + регулярные шаблоны на сегодня."""
    return await get_today_progress(db)


def _actionable_subtask_filters(today: date, parent_ids: list[int]) -> list:
    """Подзадачи для баннера: у родителей на сегодня, без DL или DL <= сегодня."""
    return [
        Task.parent_task_id.in_(parent_ids),
        Task.item_kind == "task",
        or_(Task.deadline.is_(None), Task.deadline <= today),
        or_(
            Task.status != "выполнена",
            and_(
                Task.status == "выполнена",
                Task.completed_at.isnot(None),
                func.date(Task.completed_at) == today.isoformat(),
            ),
        ),
    ]


async def get_today_actionable_stats(db: AsyncSession) -> tuple[int, int]:
    """Реальная дневная нагрузка для баннера: standalone + подзадачи родителей на сегодня.

    Подзадачи считаются если:
    - deadline не задан (работаем без DL), или
    - deadline <= сегодня (DL наступил).
    Подзадачи с DL в будущем не входят, пока дата не наступит.
    """
    today = date.today()
    completed, total = await get_today_progress(db)

    roots_result = await db.execute(select(Task).where(*_today_roots_filter(today)))
    roots = roots_result.scalars().all()
    if not roots:
        return completed, total

    root_ids = [r.id for r in roots]
    subs_result = await db.execute(
        select(Task).where(*_actionable_subtask_filters(today, root_ids))
    )
    subs = subs_result.scalars().all()

    sub_completed = sum(
        1 for s in subs
        if s.status == "выполнена" and _completed_on_day(s.completed_at, today)
    )
    return completed + sub_completed, total + len(subs)


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
        # Идём по родителям, рендерим их подзадачи как сегменты с микро-разделителями
        # Но проще: непрерывная полоска + лейбл
        # Для сегментов генерируем N элементов
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

    pct = min(int(sp["subtask_done"] / sp["subtask_total"] * 100), 100) if sp["subtask_total"] > 0 else 0

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


async def ai_warning_oob_html(db: AsyncSession) -> str:
    """HTMX OOB: жёлтый баннер нагрузки на дашборде."""
    warning = await build_daily_load_warning(db)
    if warning:
        return (
            f'<div id="ai-warning-block" hx-swap-oob="true" '
            f'class="mb-6 p-4 rounded-lg bg-yellow-900/30 border border-yellow-700 animate-pulse">'
            f'<p class="text-yellow-300">{warning}</p></div>'
        )
    return '<div id="ai-warning-block" hx-swap-oob="true" class="hidden"></div>'


async def append_today_stats_oob(content: str, db: AsyncSession) -> str:
    completed, total = await get_today_stats(db)
    sp = await get_subtask_today_progress(db)
    return (
        content
        + today_stats_oob_html(completed, total)
        + today_subtask_stats_oob_html(sp)
        + await ai_warning_oob_html(db)
    )


def _completed_tasks_base_filter(start: date, end: date):
    """Корневые задачи, закрытые в интервале дат (без recurring)."""
    return (
        Task.status == "выполнена",
        Task.completed_at.isnot(None),
        func.date(Task.completed_at) >= start.isoformat(),
        func.date(Task.completed_at) <= end.isoformat(),
        Task.parent_task_id == None,
        or_(Task.source != "recurring", Task.source == None),
        Task.item_kind == "task",
    )


def rolling_week_windows(today: date) -> tuple[tuple[date, date], tuple[date, date]]:
    """Текущее и предыдущее окно по 8 календарных дней (как график «неделя»)."""
    current_start = today - timedelta(days=7)
    prev_end = current_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=7)
    return (current_start, today), (prev_start, prev_end)


async def count_stale_in_progress_tasks(
    db: AsyncSession, *, stale_days: int = 7
) -> int:
    """Корневые задачи в статусе «в_работе» дольше stale_days (по created_at)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
    result = await db.execute(
        select(func.count(Task.id)).where(
            Task.status == "в_работе",
            Task.is_archived == False,
            Task.parent_task_id == None,
            Task.item_kind == "task",
            Task.created_at <= cutoff,
        )
    )
    return result.scalar() or 0


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
) -> float:
    """Среднее закрытых корневых задач за рабочий день (пн–пт) за период."""
    today = date.today()
    start = today - timedelta(days=lookback_days - 1)

    completed_in_period = await count_completed_tasks(
        db, start, today, workdays_only=True
    )
    workdays = count_workdays_between(start, today)
    if workdays == 0:
        return 0.0
    return completed_in_period / workdays


async def get_productivity_insights(db: AsyncSession) -> dict:
    """Сводка для блока динамики (рабочие дни, без recurring)."""
    today = date.today()
    start_30 = today - timedelta(days=29)

    avg = await get_avg_completed_per_day(db, 14)
    (cur_start, cur_end), (prev_start, prev_end) = rolling_week_windows(today)
    completed_7d = await count_completed_tasks(
        db, cur_start, cur_end, workdays_only=True
    )
    completed_prev_7d = await count_completed_tasks(
        db, prev_start, prev_end, workdays_only=True
    )
    week_delta = completed_7d - completed_prev_7d
    completed_30d = await count_completed_tasks(db, start_30, today, workdays_only=True)
    stale_in_progress = await count_stale_in_progress_tasks(db)

    history = (await get_history_data(db, "week"))["history"]
    workday_counts = [row[1] for row in history if len(row) > 2 and not row[2]]
    best_day_7d = max(workday_counts) if workday_counts else 0

    workdays_7 = count_workdays_between(cur_start, cur_end)
    expected_7d = int(round(avg * workdays_7)) if avg >= 0.5 and workdays_7 else None
    if expected_7d is not None and expected_7d > 0:
        pace_pct = round(completed_7d / expected_7d * 100)
    else:
        pace_pct = None

    if week_delta > 0:
        week_delta_label = f"+{week_delta}"
    elif week_delta < 0:
        week_delta_label = f"−{abs(week_delta)}"
    else:
        week_delta_label = "0"

    return {
        "avg_workday": int(round(avg)) if avg >= 0.5 else None,
        "completed_7d": completed_7d,
        "completed_prev_7d": completed_prev_7d,
        "completed_30d": completed_30d,
        "week_delta": week_delta,
        "week_delta_label": week_delta_label,
        "best_day_7d": best_day_7d,
        "pace_pct": pace_pct,
        "workdays_7": workdays_7,
        "stale_in_progress": stale_in_progress,
    }


async def get_subtask_insights(db: AsyncSession) -> dict:
    """Аналитика подзадач: создано / закрыто за 7 рабочих дней vs прошлая неделя."""
    today = date.today()
    (cur_start, cur_end), (prev_start, prev_end) = rolling_week_windows(today)

    # Подзадачи, созданные в периоде
    cur_created = await db.scalar(
        select(func.count(Task.id)).where(
            func.date(Task.created_at) >= cur_start.isoformat(),
            func.date(Task.created_at) <= cur_end.isoformat(),
            Task.parent_task_id.isnot(None),
            Task.item_kind == "task",
        )
    ) or 0

    prev_created = await db.scalar(
        select(func.count(Task.id)).where(
            func.date(Task.created_at) >= prev_start.isoformat(),
            func.date(Task.created_at) <= prev_end.isoformat(),
            Task.parent_task_id.isnot(None),
            Task.item_kind == "task",
        )
    ) or 0

    # Подзадачи, закрытые в периоде
    cur_closed = await db.scalar(
        select(func.count(Task.id)).where(
            Task.status == "выполнена",
            Task.completed_at.isnot(None),
            func.date(Task.completed_at) >= cur_start.isoformat(),
            func.date(Task.completed_at) <= cur_end.isoformat(),
            Task.parent_task_id.isnot(None),
            Task.item_kind == "task",
        )
    ) or 0

    prev_closed = await db.scalar(
        select(func.count(Task.id)).where(
            Task.status == "выполнена",
            Task.completed_at.isnot(None),
            func.date(Task.completed_at) >= prev_start.isoformat(),
            func.date(Task.completed_at) <= prev_end.isoformat(),
            Task.parent_task_id.isnot(None),
            Task.item_kind == "task",
        )
    ) or 0

    created_delta = cur_created - prev_created
    closed_delta = cur_closed - prev_closed

    def _delta_label(d: int) -> str:
        if d > 0:
            return f"+{d}"
        elif d < 0:
            return f"−{abs(d)}"
        return "0"

    def _delta_color(d: int) -> str:
        # Зелёный — рост закрытых (хорошо), серый — рост созданных (нейтрально)
        return "text-green-400" if d > 0 else "text-red-400/90" if d < 0 else "text-gray-400"

    return {
        "created_7d": cur_created,
        "created_prev_7d": prev_created,
        "created_delta": created_delta,
        "created_delta_label": _delta_label(created_delta),
        "closed_7d": cur_closed,
        "closed_prev_7d": prev_closed,
        "closed_delta": closed_delta,
        "closed_delta_label": _delta_label(closed_delta),
        "closed_delta_color": _delta_color(closed_delta),
    }


async def build_daily_load_warning(db: AsyncSession) -> Optional[str]:
    """Предупреждение о перегрузке — только реальные задачи на сегодня."""
    completed, total = await get_today_actionable_stats(db)
    remaining = max(total - completed, 0)
    if remaining <= 8:
        return None

    avg = await get_avg_completed_per_day(db)
    if avg >= 0.5:
        avg_label = int(round(avg))
        avg_part = f"Обычно вы закрываете ~{avg_label} в день."
    else:
        avg_part = "Обычно вы закрываете ~5 в день."

    return (
        f"Сегодня {total} задач: {completed} готово, {remaining} осталось. {avg_part}"
    )


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
    """ShoppingItem(reading) → dict {id, title, url, content, status, pages_total, pages_read} для шаблона."""
    return [{
        "id": it.id,
        "title": it.title,
        "url": _reading_url(it.title),
        "content": it.content,
        "status": it.reading_status or "want_to_read",
        "pages_total": it.pages_total,
        "pages_read": it.pages_read or 0,
    } for it in items]


def _render_reading_list(request: Request, reading_items: list) -> str:
    tpl = templates.get_template("partials/reading_list.html")
    return tpl.render({"request": request, "reading_items": reading_items})


async def _reading_list_response(request: Request, db: AsyncSession):
    from fastapi.responses import HTMLResponse
    from app.services.shopping_service import load_active_reading

    items = await load_active_reading(db)
    html = _render_reading_list(request, reading_items_view(items))
    return HTMLResponse(content=html)


async def get_history_data(db, period: str):
    """Данные для графика динамики (неделя/месяц — все календарные дни, выходные помечены)."""
    today = date.today()

    if period == "year":
        start_date = today.replace(day=1) - timedelta(days=365)
        query = (
            select(func.strftime("%Y-%m", Task.completed_at), func.count(Task.id))
            .where(Task.completed_at >= start_date, Task.status == "выполнена")
            .group_by(func.strftime("%Y-%m", Task.completed_at))
            .order_by(func.strftime("%Y-%m", Task.completed_at).asc())
        )
        result = await db.execute(query)
        history = list(result.all())
        max_val = max([count for _, count in history] + [1])
        return {"history": history, "max_val": max_val}

    if period == "month":
        start_date = today - timedelta(days=30)
    else:
        start_date = today - timedelta(days=7)

    query = (
        select(func.date(Task.completed_at), func.count(Task.id))
        .where(
            func.date(Task.completed_at) >= start_date.isoformat(),
            func.date(Task.completed_at) <= today.isoformat(),
            Task.status == "выполнена",
        )
        .group_by(func.date(Task.completed_at))
        .order_by(func.date(Task.completed_at).asc())
    )
    result = await db.execute(query)
    counts_by_date: dict[str, int] = {}
    for day_key, count in result.all():
        key = day_key if isinstance(day_key, str) else day_key.isoformat()
        counts_by_date[key] = count

    history = []
    d = start_date
    while d <= today:
        ds = d.isoformat()
        history.append((ds, counts_by_date.get(ds, 0), is_weekend(d)))
        d += timedelta(days=1)

    max_val = max([count for _, count, *_ in history] + [1])
    return {"history": history, "max_val": max_val}

async def get_tasks_today(db: AsyncSession, request: Request):
    """Вспомогательная функция для получения списка задач на сегодня и их отрисовки"""
    today = date.today()

    result = await db.execute(
        select(Task)
        .options(selectinload(Task.category).selectinload(Category.parent))
        .where(
            Task.due_date == today,
            Task.is_archived == False,
            Task.status.in_(["новая", "в_работе"]),
            Task.parent_task_id == None,
            Task.source.is_distinct_from("recurring"),
            Task.item_kind == "task",
        ).order_by(*dashboard_task_order_by())
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
    content = template.render({
        "request": request,
        "tasks_with_subtasks": tasks_with_subtasks,
        "standalone_tasks": standalone_tasks,
        "subtasks_map": subtasks_map,
    })

    return await append_today_stats_oob(content, db)

__all__ = [
    "templates",
    "compute_period_data",
    "get_categories_list",
    "get_today_stats",
    "get_today_progress",
    "get_today_actionable_stats",
    "get_subtask_today_progress",
    "today_stats_oob_html",
    "today_subtask_stats_oob_html",
    "append_today_stats_oob",
    "load_subtasks_map",
    "repair_archived_subtasks",
    "build_daily_load_warning",
    "get_avg_completed_per_day",
    "get_productivity_insights",
    "get_subtask_insights",
    "get_history_data",
    "get_tasks_today",
    "dashboard_task_order_by",
    "_strip_emoji",
    "_render_shopping_list",
    "_shopping_stats_oob",
    "_shopping_list_response",
    "_shopping_toggle_response",
    "_shopping_counts",
    "reading_items_view",
    "_render_reading_list",
    "_reading_list_response",
]
