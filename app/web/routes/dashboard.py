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
from app.models.task import Task
from app.models.category import Category
from app.models.recurring import RecurringTask
from app.models.shopping import ShoppingItem
from app.models.report import AIReport
from app.models.finance import Transaction
from app.config import settings

from app.web.deps import (
    templates,
    compute_period_data,
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
)

router = APIRouter()

from app.services.rollover_service import rollover_overdue_tasks
from app.services.ai_service import ai_service

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Дашборд — задачи на сегодня"""
    from app.models.recurring import RecurringTask
    today = date.today()

    # Автоматический перенос просроченных задач при открытии дашборда
    rollover_result = await rollover_overdue_tasks()
    if rollover_result["moved"] > 0:
        from app.utils.logger import app_logger
        app_logger.info(f"🔄 Auto-rollover: перенесено {rollover_result['moved']} задач на сегодня")

    async with async_session() as db:
        # Привычки (Habit Tracker)
        from app.models.habit import Habit
        from app.models.habit_log import HabitLog
        from sqlalchemy import and_

        habits_result = await db.execute(
            select(Habit).where(Habit.is_active == True, Habit.is_archived == False)
        )
        habits = habits_result.scalars().all()
        
        # Для каждой привычки формируем сетку из 30 дней от её даты старта
        habits_data = []
        for h in habits:
            h_start = h.start_date or today
            h_dates = [(h_start + timedelta(days=i)) for i in range(h.target_days or 30)]
            
            # Определяем смещение (день недели начала: 0=Пн, 1=Вт, ... 6=Вс)
            start_weekday = h_start.weekday()
            
            # Логи для этой конкретной привычки и ТЕКУЩЕГО цикла
            h_logs_result = await db.execute(
                select(HabitLog.date).where(
                    HabitLog.habit_id == h.id,
                    HabitLog.cycle_number == h.current_cycle
                )
            )
            h_logs = {log_date.isoformat() for log_date in h_logs_result.scalars().all()}
            
            habits_data.append({
                "habit": h,
                "dates": h_dates,
                "logs": h_logs,
                "progress": len(h_logs),
                "start_weekday": start_weekday
            })

        # Period tracker data
        from app.models.period_entry import PeriodEntry
        period_result = await db.execute(select(PeriodEntry).order_by(PeriodEntry.date))
        period_entries = period_result.scalars().all()
        period_data = compute_period_data(list(period_entries), today)

        # Обычные задачи (только корневые)
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
        tasks = list(result.scalars().all())

        await repair_archived_subtasks(db)
        subtasks_map = await load_subtasks_map(db, [t.id for t in tasks])
        await db.commit()

        # Разделяем задачи: с подзадачами и standalone
        tasks_with_subtasks = [t for t in tasks if subtasks_map.get(t.id)]
        standalone_tasks = [t for t in tasks if not subtasks_map.get(t.id)]

        from app.services.shopping_service import load_active_shopping, load_active_reading

        bundle = await get_dashboard_day_stats(db, today)
        recurring_today = bundle.recurring_today
        completed, total = bundle.completed, bundle.total
        subtask_progress = bundle.subtask_progress
        ai_warning = bundle.ai_warning

        shopping_items = await load_active_shopping(db)
        reading_items = await load_active_reading(db)

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
    })


@router.get("/dashboard/today-stats", response_class=HTMLResponse)
async def dashboard_today_stats():
    """HTMX OOB-фрагмент: счётчик и полоска прогресса дня."""
    async with async_session() as db:
        return HTMLResponse(content=await append_today_stats_oob("", db))
