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
    get_today_stats,
    get_history_data,
    get_tasks_today,
    _strip_emoji,
    _render_shopping_list,
    _shopping_stats_script,
    _shopping_list_response,
)

router = APIRouter()


@router.get("/backlog", response_class=HTMLResponse)
async def backlog_page(request: Request):
    """Бэклог — задачи без даты"""
    async with async_session() as db:
        result = await db.execute(
            select(Task)
            .options(selectinload(Task.category).selectinload(Category.parent))
            .where(
                Task.is_archived == False,
                Task.due_date == None,
                Task.parent_task_id == None  # Только корневые
            )
            .order_by(Task.created_at.desc())
        )
        tasks = list(result.scalars().all())

        # Загружаем подзадачи для бэклога
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

    return templates.TemplateResponse(request, "backlog.html", {
        "request": request,
        "tasks": tasks,
        "subtasks_map": subtasks_map,  # Передаем словарь подзадач
    })


@router.post("/backlog/{task_id}/make-recurring-form", response_class=HTMLResponse)
async def show_make_recurring_form(request: Request, task_id: int):
    """Показать форму для превращения задачи в периодическую"""
    return HTMLResponse(f"""
        <div class="bg-dark-800 rounded-lg p-4 border border-purple-600 transition" id="task-{task_id}">
            <form hx-post="/backlog/{task_id}/make-recurring"
                  hx-target="#task-{task_id}"
                  hx-swap="outerHTML"
                  class="space-y-3">
                <p class="text-sm text-white font-medium">🔄 Сделать периодической:</p>
                
                <select name="recurrence_type" onchange="this.nextElementSibling.classList.toggle('hidden', this.value !== 'weekly')"
                        class="w-full bg-dark-900 border border-dark-700 rounded px-3 py-2 text-white text-sm">
                    <option value="daily">📅 Ежедневно</option>
                    <option value="weekly">📆 Еженедельно</option>
                    <option value="monthly">🗓 Ежемесячно</option>
                </select>

                <div class="hidden flex flex-wrap gap-2 text-xs text-gray-400">
                    <label><input type="checkbox" name="recurrence_days" value="mon" class="accent-purple-500"> Пн</label>
                    <label><input type="checkbox" name="recurrence_days" value="tue" class="accent-purple-500"> Вт</label>
                    <label><input type="checkbox" name="recurrence_days" value="wed" class="accent-purple-500"> Ср</label>
                    <label><input type="checkbox" name="recurrence_days" value="thu" class="accent-purple-500"> Чт</label>
                    <label><input type="checkbox" name="recurrence_days" value="fri" class="accent-purple-500"> Пт</label>
                    <label><input type="checkbox" name="recurrence_days" value="sat" class="accent-purple-500"> Сб</label>
                    <label><input type="checkbox" name="recurrence_days" value="sun" class="accent-purple-500"> Вс</label>
                </div>

                <div class="flex gap-2">
                    <button type="submit" class="flex-1 bg-purple-600 hover:bg-purple-500 text-white py-1.5 rounded text-sm font-medium">Создать</button>
                    <button type="button" hx-get="/backlog" hx-target="#backlog-list" class="px-3 py-1.5 bg-dark-700 text-gray-400 rounded text-sm">Отмена</button>
                </div>
            </form>
        </div>
    """)


@router.post("/backlog/{task_id}/make-recurring", response_class=HTMLResponse)
async def make_task_recurring(
    task_id: int,
    recurrence_type: str = Form(...),
    recurrence_days: List[str] = Form(None),
):
    """Создать периодическую задачу и удалить из бэклога"""
    async with async_session() as db:
        # 1. Находим исходную задачу
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        
        if not task:
            return HTMLResponse(f'<div id="task-{task_id}" class="hidden"></div>')

        # 2. Проверка на дубликат (title + recurrence_type)
        existing = await db.execute(
            select(RecurringTask).where(
                RecurringTask.title == task.title,
                RecurringTask.recurrence_type == recurrence_type,
            )
        )
        if existing.scalar_one_or_none():
            # Если уже есть такой шаблон, просто удаляем задачу из бэклога
            await db.delete(task)
            await db.commit()
            return HTMLResponse(f'<div id="task-{task_id}" class="hidden"></div>')

        # 3. Создаем RecurringTask
        recurring = RecurringTask(
            title=task.title,
            description=task.description,
            category_id=task.category_id,
            priority=task.priority,
            recurrence_type=recurrence_type,
            recurrence_days=recurrence_days if recurrence_type == "weekly" and recurrence_days else None,
            start_date=date.today(),
            is_active=True,
        )
        db.add(recurring)

        # 4. Удаляем старую задачу из бэклога
        await db.delete(task)
        await db.commit()

        # 4. Возвращаем пустой блок (HTMX удалит элемент из списка)
        return HTMLResponse(f'<div id="task-{task_id}" class="hidden"></div>')


@router.post("/backlog/{task_id}/plan-today", response_class=HTMLResponse)
async def plan_task_today(task_id: int):
    """Мгновенно перенести задачу из бэклога на сегодня"""
    today = date.today()
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.due_date = today
            task.status = "новая"
            await db.commit()
            return HTMLResponse(f'<div id="task-{task_id}" class="hidden"></div>')
    return HTMLResponse(f'<div id="task-{task_id}" class="text-red-400">Ошибка</div>')
