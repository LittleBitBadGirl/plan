from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from sqlalchemy import select, func, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, datetime, time, timedelta
from typing import List, Optional
import re
import json

from app.db.database import async_session
from app.models.task import Task, task_is_active
from app.models.category import Category
from app.models.recurring import RecurringTask
from app.models.shopping import ShoppingItem
from app.models.report import AIReport
from app.models.finance import Transaction
from app.config import settings

from app.web.deps import (
    templates,
    compute_period_data,
    load_period_entries_for_dashboard,
    get_categories_list,
    get_dashboard_day_stats,
    get_history_data,
    get_tasks_today,
    append_today_stats_oob,
    load_subtasks_map,
    repair_archived_subtasks,
    dashboard_task_order_by,
    _strip_emoji,
    _render_shopping_list,
    _shopping_stats_oob,
    _shopping_list_response,
    reading_items_view,
    is_weekend,
    not_work_task_filter,
    work_task_filter,
)

router = APIRouter()

from app.services.ai_service import ai_service

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Дашборд — задачи на сегодня"""
    from app.models.recurring import RecurringTask
    from app.api.habits import build_habit_cycle_grid, load_habit_logs_map
    from app.models.habit import Habit

    today = date.today()

    # На выходных рабочие задачи не показываем: Вера не хочет возвращаться в работу.
    # Кнопка «Показать рабочие» ставит cookie show_work=1 — тогда показываем снова.
    show_work = request.cookies.get("show_work") == "1"
    hide_work = is_weekend(today) and not show_work

    async with async_session() as db:
        habits_result = await db.execute(
            select(Habit).where(Habit.is_active == True, Habit.is_archived == False)
        )
        habits = list(habits_result.scalars().all())
        logs_map = await load_habit_logs_map(db, habits)

        habits_data = []
        for h in habits:
            grid = build_habit_cycle_grid(h, today)
            h_logs = logs_map.get(h.id, set())
            habits_data.append({
                "habit": h,
                "dates": grid["dates"],
                "logs": h_logs,
                "progress": len(h_logs),
                "start_weekday": grid["start_weekday"],
            })

        # Period tracker data (последние 120 дней — достаточно для фазы и календаря)
        period_entries = await load_period_entries_for_dashboard(db, today)
        period_data = compute_period_data(period_entries, today)

        # Обычные задачи (только корневые)
        base_task_filters = [
            Task.due_date == today,
            task_is_active(),
            Task.status.in_(["новая", "в_работе"]),
            Task.parent_task_id == None,
            Task.source.is_distinct_from("recurring"),
            Task.item_kind == "task",
        ]
        task_filters = list(base_task_filters)
        if hide_work:
            task_filters.append(not_work_task_filter())

        result = await db.execute(
            select(Task)
            .options(selectinload(Task.category).selectinload(Category.parent))
            .where(*task_filters)
            .order_by(*dashboard_task_order_by())
        )
        tasks = list(result.scalars().all())

        # Сколько рабочих задач спрятано — чтобы кнопка говорила правду.
        hidden_work_count = 0
        if is_weekend(today):
            hidden_result = await db.execute(
                select(func.count(Task.id)).where(
                    *base_task_filters,
                    work_task_filter(),
                )
            )
            hidden_work_count = hidden_result.scalar() or 0

        await repair_archived_subtasks(db)
        subtasks_map = await load_subtasks_map(db, [t.id for t in tasks])
        await db.commit()

        # Разделяем задачи: с подзадачами и standalone
        tasks_with_subtasks = [t for t in tasks if subtasks_map.get(t.id)]
        standalone_tasks = [t for t in tasks if not subtasks_map.get(t.id)]

        from app.services.shopping_service import load_active_shopping, load_active_reading

        bundle = await get_dashboard_day_stats(db, today, hide_work=hide_work)
        recurring_today = bundle.recurring_today
        completed, total = bundle.completed, bundle.total
        subtask_progress = bundle.subtask_progress
        ai_warning = bundle.ai_warning

        shopping_items = await load_active_shopping(db)
        reading_items = await load_active_reading(db)

        # Менеджеры: блок обратной связи (рендер сервером, без hx-trigger load)
        from app.services.manager_feedback_service import build_widget_context

        managers_widget = await build_widget_context(db)

        # Категории для формы — только задачные, финансовые не смешиваем
        cats_result = await db.execute(
            select(Category)
            .where(Category.type == 'task')
            .order_by(Category.is_global.desc(), Category.name)
        )
        categories = cats_result.scalars().all()

        calendar_events = []
        calendar_personal_events = []
        from app.services.calendar_sync_service import (
            calendar_sync_active,
            get_visible_events_grouped,
        )

        calendar_events = []
        calendar_personal_events = []
        try:
            calendar_events, calendar_personal_events = await get_visible_events_grouped(
                db, today
            )
        except Exception as exc:
            from app.utils.logger import app_logger
            app_logger.warning(f"Calendar events skipped: {exc}")

    return templates.TemplateResponse(request, "dashboard.html", {
        "request": request,
        "tasks": tasks,
        "weekend_hide_work": hide_work,
        "weekend_show_work": is_weekend(today) and show_work,
        "hidden_work_count": hidden_work_count,
        "tasks_with_subtasks": tasks_with_subtasks,
        "standalone_tasks": standalone_tasks,
        "subtasks_map": subtasks_map,
        "recurring_tasks": recurring_today,
        "categories": categories,
        "completed": completed,
        "total": total,
        "subtask_progress": subtask_progress,
        "today": today,
        "ai_warning": ai_warning,
        "habits_data": habits_data,
        "period_data": period_data,
        "shopping_items": shopping_items,
        "reading_items": reading_items_view(reading_items),
        "shop_stats": {
            "total": len(shopping_items),
            "remaining": len(shopping_items),
        },
        "calendar_events": calendar_events,
        "calendar_personal_events": calendar_personal_events,
        "calendar_sync_active": calendar_sync_active(),
        "managers_widget": managers_widget,
    })


@router.get("/dashboard/today-stats", response_class=HTMLResponse)
async def dashboard_today_stats(request: Request):
    """HTMX OOB-фрагмент: счётчики «активно / закрыто сегодня»."""
    async with async_session() as db:
        return HTMLResponse(content=await append_today_stats_oob("", db, request))
