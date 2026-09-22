from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from sqlalchemy import select, func, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, datetime, time, timedelta
from typing import List, Optional
from collections import defaultdict
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
    get_categories_list,
    get_today_stats,
    get_history_data,
    get_tasks_today,
    _strip_emoji,
    _render_shopping_list,
    _shopping_stats_oob,
    _shopping_list_response,
)

from app.services.ai_service import ai_service

router = APIRouter()


async def _load_backlog(db: AsyncSession) -> tuple[list[Task], dict[int, list[Task]]]:
    """Задачи бэклога и их подзадачи."""
    result = await db.execute(
        select(Task)
        .options(selectinload(Task.category).selectinload(Category.parent))
        .where(
            task_is_active(),
            Task.due_date == None,
            Task.parent_task_id == None,
        )
        .order_by(Task.created_at.desc())
    )
    tasks = list(result.scalars().all())

    subtasks_map: dict[int, list[Task]] = defaultdict(list)
    if tasks:
        task_ids = [t.id for t in tasks]
        subtasks_result = await db.execute(
            select(Task).where(Task.parent_task_id.in_(task_ids), task_is_active())
        )
        for st in subtasks_result.scalars().all():
            subtasks_map[st.parent_task_id].append(st)

    return tasks, subtasks_map


async def _render_backlog_list(request: Request, db: AsyncSession) -> str:
    tasks, subtasks_map = await _load_backlog(db)
    return templates.get_template("partials/backlog_list.html").render({
        "request": request,
        "tasks": tasks,
        "subtasks_map": subtasks_map,
    })


async def _load_task_categories(db: AsyncSession) -> dict:
    """Категории задач и счётчики — для вкладки «Категории» внутри Бэклога.

    У каждой категории два числа: активные задачи и всего задач в ней (общий
    объём — включая выполненные и архив). Вера читает это как «сколько сейчас /
    сколько всего было».
    """
    result = await db.execute(
        select(Category).order_by(Category.is_global.desc(), Category.name)
    )
    categories = list(result.scalars().all())

    # «Активные» = не в архиве и ещё не завершены: Вера читает это число как
    # «сколько сейчас в работе», а не «сколько строк в категории».
    counts_result = await db.execute(
        select(Task.category_id, func.count(Task.id))
        .where(task_is_active(), Task.completed_at.is_(None))
        .group_by(Task.category_id)
    )
    task_counts = {row[0]: row[1] for row in counts_result.all()}

    totals_result = await db.execute(
        select(Task.category_id, func.count(Task.id)).group_by(Task.category_id)
    )
    total_counts = {row[0]: row[1] for row in totals_result.all()}

    task_cats = [c for c in categories if c.type == "task"]
    global_cats = [c for c in task_cats if c.is_global]
    sub_cats = {gc.id: [c for c in task_cats if c.parent_id == gc.id] for gc in global_cats}

    # Подкатегории сворачиваем в родителя — отдельно активные и «всего»
    final_counts = dict(task_counts)
    final_totals = dict(total_counts)
    for cat in categories:
        if not cat.is_global and cat.parent_id:
            active = task_counts.get(cat.id, 0)
            if active > 0:
                final_counts[cat.parent_id] = final_counts.get(cat.parent_id, 0) + active
            everything = total_counts.get(cat.id, 0)
            if everything > 0:
                final_totals[cat.parent_id] = final_totals.get(cat.parent_id, 0) + everything

    return {
        "global_categories": global_cats,
        "sub_categories": sub_cats,
        "task_counts": final_counts,          # активные, с подкатегориями
        "raw_counts": task_counts,            # активные в самой категории
        "total_counts": final_totals,         # всего, с подкатегориями
        "raw_total_counts": total_counts,     # всего в самой категории
    }


BACKLOG_VIEWS = ("tasks", "calendar", "categories")


@router.get("/backlog", response_class=HTMLResponse)
async def backlog_page(request: Request, view: str = "tasks"):
    """Бэклог: задачи без даты, месячный календарь и категории задач — вкладками.

    Календарь раньше был отдельной страницей, категории задач — отдельной
    страницей «Категории». Обе живут здесь, рядом с самими задачами.
    """
    view = view if view in BACKLOG_VIEWS else "tasks"

    async with async_session() as db:
        tasks, subtasks_map = await _load_backlog(db)
        categories_ctx = await _load_task_categories(db) if view == "categories" else {}

    context = {
        "request": request,
        "tasks": tasks,
        "subtasks_map": subtasks_map,
        "categories": await get_categories_list(),
        "view": view,
    }
    context.update(categories_ctx)

    return templates.TemplateResponse(request, "backlog.html", context)


@router.post("/backlog/create", response_class=HTMLResponse)
async def backlog_create_htmx(
    request: Request,
    title: str = Form(...),
    category_id: str = Form(None),
):
    """HTMX: быстрое создание задачи в бэклог (без даты)"""
    title = title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Пустой заголовок")

    async with async_session() as db:
        final_category_id = None
        if category_id and category_id.isdigit():
            final_category_id = int(category_id)
        else:
            cat_stmt = select(Category).order_by(Category.is_global.desc(), Category.name)
            cat_res = await db.execute(cat_stmt)
            all_cats = [{"id": c.id, "name": c.name, "is_global": c.is_global} for c in cat_res.scalars().all()]
            ai_result = await ai_service.categorize(title, all_cats)
            if ai_result and ai_result.get("category_id"):
                final_category_id = int(ai_result["category_id"])

        task = Task(
            title=title,
            category_id=final_category_id,
            due_date=None,
            source="web",
            status="новая",
        )
        db.add(task)
        await db.commit()

        return HTMLResponse(content=await _render_backlog_list(request, db))


@router.post("/backlog/{task_id}/make-recurring-form", response_class=HTMLResponse)
async def show_make_recurring_form(request: Request, task_id: int):
    """Показать форму для превращения задачи в периодическую"""
    return HTMLResponse(f"""
        <div class="px-3 py-2 bg-purple-950/30 border-l-2 border-purple-500" id="task-{task_id}">
            <form hx-post="/backlog/{task_id}/make-recurring"
                  hx-target="#task-{task_id}"
                  hx-swap="outerHTML"
                  class="flex flex-wrap items-center gap-2">
                <span class="text-xs text-purple-300 font-medium shrink-0">Шаблон:</span>
                <select name="recurrence_type" onchange="this.nextElementSibling.classList.toggle('hidden', this.value !== 'weekly')"
                        class="bg-dark-900 border border-dark-600 rounded px-2 py-1 text-white text-xs min-w-[7rem]">
                    <option value="daily">Ежедневно</option>
                    <option value="weekly">Еженедельно</option>
                    <option value="monthly">Ежемесячно</option>
                </select>
                <div class="hidden flex flex-wrap gap-1.5 text-[10px] text-gray-400">
                    <label><input type="checkbox" name="recurrence_days" value="mon" class="accent-purple-500"> Пн</label>
                    <label><input type="checkbox" name="recurrence_days" value="tue" class="accent-purple-500"> Вт</label>
                    <label><input type="checkbox" name="recurrence_days" value="wed" class="accent-purple-500"> Ср</label>
                    <label><input type="checkbox" name="recurrence_days" value="thu" class="accent-purple-500"> Чт</label>
                    <label><input type="checkbox" name="recurrence_days" value="fri" class="accent-purple-500"> Пт</label>
                    <label><input type="checkbox" name="recurrence_days" value="sat" class="accent-purple-500"> Сб</label>
                    <label><input type="checkbox" name="recurrence_days" value="sun" class="accent-purple-500"> Вс</label>
                </div>
                <button type="submit" class="px-2.5 py-1 bg-purple-600 hover:bg-purple-500 text-white rounded text-xs font-bold">Создать</button>
                <button type="button" onclick="window.location.reload()" class="px-2 py-1 text-gray-500 hover:text-gray-300 text-xs">Отмена</button>
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
            task.postpones = 0
            task.status = "новая"
            await db.commit()
            return HTMLResponse(f'<div id="task-{task_id}" class="hidden"></div>')
    return HTMLResponse(f'<div id="task-{task_id}" class="text-red-400">Ошибка</div>')
