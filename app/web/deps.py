"""Shared web dependencies: templates, filters, helpers."""
from collections import defaultdict
from pathlib import Path
import re
from statistics import mean
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import Request
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
    """Обычный календарь текущего месяца — цифры = числа месяца."""
    import calendar as _cal

    year, month = today.year, today.month
    month_label = f"{_MONTH_NAMES[month]} {year}"
    start_weekday = date(year, month, 1).weekday()
    days_in_month = _cal.monthrange(year, month)[1]

    calendar_days = []
    for day_num in range(1, days_in_month + 1):
        d = date(year, month, day_num)
        if d in period_map:
            state = "pain" if period_map[d] else "period"
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
    """
    period_map = {e.date: e.has_pain for e in entries} if entries else {}

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
            "month_label": month_label,
            "start_weekday": start_weekday,
            "calendar_days": calendar_days,
            "days_until_next": None,
            "cycles_history": [],
        }

    sorted_entries = sorted(entries, key=lambda e: e.date)

    cycles: list[list] = []
    group = [sorted_entries[0]]
    for entry in sorted_entries[1:]:
        if (entry.date - group[-1].date).days <= 2:
            group.append(entry)
        else:
            cycles.append(group)
            group = [entry]
    cycles.append(group)

    cycle_starts = [g[0].date for g in cycles]
    cycle_lengths = [
        (cycle_starts[i + 1] - cycle_starts[i]).days
        for i in range(len(cycle_starts) - 1)
    ]

    avg_cycle = round(mean(cycle_lengths)) if cycle_lengths else 28
    avg_period = max(1, round(mean(len(g) for g in cycles)))
    last_start = cycle_starts[-1]
    current_day = (today - last_start).days + 1
    current_phase = _period_phase(current_day, avg_cycle, avg_period)
    days_until_next = avg_cycle - current_day if cycle_lengths else None

    month_label, start_weekday, calendar_days = _build_month_calendar(today, period_map)

    cycles_history = []
    for i, grp in enumerate(cycles):
        cl = cycle_lengths[i] if i < len(cycle_lengths) else None
        pain_count = sum(1 for e in grp if e.has_pain)
        cycles_history.append({
            "num": i + 1,
            "start": grp[0].date.strftime("%d.%m.%Y"),
            "length": cl,
            "period_days": len(grp),
            "pain_days": pain_count,
            "is_current": cl is None,
        })

    return {
        "has_data": True,
        "last_period_start": last_start,
        "current_cycle_day": current_day,
        "current_phase": current_phase,
        "current_phase_label": _PHASE_LABELS.get(current_phase, "—"),
        "avg_cycle": avg_cycle,
        "avg_period": avg_period,
        "month_label": month_label,
        "start_weekday": start_weekday,
        "calendar_days": calendar_days,
        "days_until_next": days_until_next,
        "cycles_history": cycles_history,
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


def _render_shopping_list(request: Request, items: list) -> str:
    tpl = templates.get_template("partials/shopping_list.html")
    return tpl.render({"request": request, "items": items})


async def get_categories_list():
    """Получить список категорий только для задач"""
    async with async_session() as db:
        result = await db.execute(
            select(Category).where(Category.type == 'task').order_by(Category.is_global.desc(), Category.name)
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
    """Прогресс дня: Y = карточки на сегодня, X = закрытые задачи + чекнутые подзадачи."""
    today = date.today()

    roots_result = await db.execute(select(Task).where(*_today_roots_filter(today)))
    roots = roots_result.scalars().all()
    if not roots:
        return 0, 0

    total = len(roots)

    root_ids = [r.id for r in roots]
    subs_result = await db.execute(
        select(Task).where(Task.parent_task_id.in_(root_ids))
    )
    subs_by_parent: dict[int, list[Task]] = defaultdict(list)
    for sub in subs_result.scalars().all():
        subs_by_parent[sub.parent_task_id].append(sub)

    completed = 0
    for root in roots:
        subs = subs_by_parent.get(root.id, [])
        if subs:
            if root.status == "выполнена" and _completed_on_day(root.completed_at, today):
                completed += 1
            else:
                for sub in subs:
                    if sub.status == "выполнена" and _completed_on_day(sub.completed_at, today):
                        completed += 1
        elif root.status == "выполнена" and _completed_on_day(root.completed_at, today):
            completed += 1

    return completed, total


async def get_today_stats(db: AsyncSession):
    """Статистика сегодняшнего дня (колонка 1, без recurring-вхождений)."""
    return await get_today_progress(db)


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


async def build_daily_load_warning(
    db: AsyncSession, completed: int, total: int
) -> Optional[str]:
    """Предупреждение о перегрузке — те же цифры, что в «Прогресс сегодня»."""
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


def _shopping_stats_script(total: int, archived_count: int) -> str:
    return f'''<script>
        const tc = document.getElementById('total-count');
        const rc = document.getElementById('remaining-count');
        const ac = document.getElementById('archived-count');
        if (tc) tc.textContent = '{total}';
        if (rc) rc.textContent = '{total}';
        if (ac) ac.textContent = '{archived_count}';
    </script>'''


async def _shopping_list_response(request: Request, db: AsyncSession):
    from fastapi.responses import HTMLResponse
    from app.models.shopping import ShoppingItem
    from app.services.shopping_service import load_active_shopping

    items = await load_active_shopping(db)
    archived_count_result = await db.execute(
        select(func.count(ShoppingItem.id)).where(ShoppingItem.is_archived == True)
    )
    archived_count = archived_count_result.scalar() or 0
    html = _render_shopping_list(request, items) + _shopping_stats_script(len(items), archived_count)
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
        ).order_by(Task.sort_order.asc(), Task.created_at.asc())
    )
    tasks = result.scalars().all()

    await repair_archived_subtasks(db)
    task_ids = [t.id for t in tasks]
    subtasks_map = await load_subtasks_map(db, task_ids)
    await db.commit()

    template = templates.get_template("partials/tasks_list.html")
    content = template.render({"request": request, "tasks": tasks, "subtasks_map": subtasks_map})

    completed, total = await get_today_stats(db)
    stats_oob = f'<span id="today-stats-counter" hx-swap-oob="true">{completed}/{total}</span>'
    return content + stats_oob

__all__ = [
    "templates",
    "compute_period_data",
    "get_categories_list",
    "get_today_stats",
    "get_today_progress",
    "load_subtasks_map",
    "repair_archived_subtasks",
    "build_daily_load_warning",
    "get_avg_completed_per_day",
    "get_productivity_insights",
    "get_history_data",
    "get_tasks_today",
    "_strip_emoji",
    "_render_shopping_list",
    "_shopping_stats_script",
    "_shopping_list_response",
]
