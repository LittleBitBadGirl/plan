"""Shared web dependencies: templates, filters, helpers."""
from pathlib import Path
import re
from statistics import mean
from datetime import date, timedelta
from typing import List, Optional

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, or_
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


async def get_today_stats(db: AsyncSession):
    """Статистика сегодняшнего дня (колонка 1, без recurring-вхождений)."""
    today = date.today()
    base_filter = _today_task_base_filter(today)

    completed_result = await db.execute(
        select(func.count(Task.id)).where(
            *base_filter,
            Task.status == "выполнена",
        )
    )
    completed = completed_result.scalar() or 0

    total_result = await db.execute(
        select(func.count(Task.id)).where(
            *base_filter,
            (Task.is_archived == False) | (Task.status == "выполнена"),
        )
    )
    total = total_result.scalar() or 0
    return completed, total


async def get_avg_completed_per_day(
    db: AsyncSession, lookback_days: int = 14
) -> float:
    """Среднее число закрытых корневых задач в календарный день за период."""
    today = date.today()
    start = today - timedelta(days=lookback_days - 1)

    result = await db.execute(
        select(func.count(Task.id)).where(
            Task.status == "выполнена",
            Task.completed_at.isnot(None),
            func.date(Task.completed_at) >= start,
            func.date(Task.completed_at) <= today,
            Task.parent_task_id == None,
            or_(Task.source != "recurring", Task.source == None),
            Task.item_kind == "task",
        )
    )
    completed_in_period = result.scalar() or 0
    return completed_in_period / lookback_days


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
    """Вспомогательная функция для получения данных истории"""
    from datetime import timedelta
    today = date.today()
    
    if period == "year":
        # Группировка по месяцам за последний год
        start_date = today.replace(day=1) - timedelta(days=365)
        query = (
            select(func.strftime("%Y-%m", Task.completed_at), func.count(Task.id))
            .where(Task.completed_at >= start_date, Task.status == "выполнена")
            .group_by(func.strftime("%Y-%m", Task.completed_at))
            .order_by(func.strftime("%Y-%m", Task.completed_at).asc())
        )
    elif period == "month":
        # Группировка по дням за последние 30 дней
        start_date = today - timedelta(days=30)
        query = (
            select(func.date(Task.completed_at), func.count(Task.id))
            .where(Task.completed_at >= start_date, Task.status == "выполнена")
            .group_by(func.date(Task.completed_at))
            .order_by(func.date(Task.completed_at).asc())
        )
    else: # week
        start_date = today - timedelta(days=7)
        query = (
            select(func.date(Task.completed_at), func.count(Task.id))
            .where(Task.completed_at >= start_date, Task.status == "выполнена")
            .group_by(func.date(Task.completed_at))
            .order_by(func.date(Task.completed_at).asc())
        )

    result = await db.execute(query)
    history = result.all()
    max_val = max([count for _, count in history] + [1])
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

    subtasks_map = {}
    if tasks:
        task_ids = [t.id for t in tasks]
        subtasks_result = await db.execute(
            select(Task).where(Task.parent_task_id.in_(task_ids))
        )
        all_subtasks = subtasks_result.scalars().all()
        from collections import defaultdict
        subtasks_map = defaultdict(list)
        for st in all_subtasks:
            subtasks_map[st.parent_task_id].append(st)

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
    "build_daily_load_warning",
    "get_avg_completed_per_day",
    "get_history_data",
    "get_tasks_today",
    "_strip_emoji",
    "_render_shopping_list",
    "_shopping_stats_script",
    "_shopping_list_response",
]
