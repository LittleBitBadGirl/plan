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
    load_subtasks_map,
    repair_archived_subtasks,
    _strip_emoji,
    _render_shopping_list,
    _shopping_stats_script,
    _shopping_list_response,
)

router = APIRouter()

from app.services.ai_service import ai_service

@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request, status: Optional[str] = None):
    """Все задачи"""
    async with async_session() as db:
        filters = [Task.is_archived == False, Task.due_date != None]
        if status:
            filters.append(Task.status == status)
        result = await db.execute(
            select(Task)
            .options(selectinload(Task.category))
            .where(*filters)
            .order_by(Task.due_date.asc())
        )
        tasks = result.scalars().all()
        categories = await get_categories_list()

    return templates.TemplateResponse(request, "tasks.html", {
        "request": request,
        "tasks": tasks,
        "categories": categories,
        "total": len(tasks),
        "status_filter": status,
        "category_id_filter": None,
        "from_date_filter": None,
        "to_date_filter": None,
        "has_prev": False,
        "has_next": False,
        "prev_offset": 0,
        "next_offset": 0,
    })


# ---- Web CRUD form-data (перед {task_id} роутами!) ----

@router.delete("/tasks/{task_id}/subtask", response_class=HTMLResponse)
async def delete_subtask(request: Request, task_id: int):
    """Удалить подзадачу и вернуть обновленный список"""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        
        if task:
            parent_id = task.parent_task_id
            await db.delete(task)
            await db.commit()

            subtasks_result = await db.execute(
                select(Task).where(Task.parent_task_id == parent_id).order_by(Task.created_at.asc())
            )
            subtasks = subtasks_result.scalars().all()

            return templates.TemplateResponse(request, "partials/subtasks.html", {
                "request": request,
                "subtasks": subtasks,
                "parent_id": parent_id,
            })
            
    return HTMLResponse("Ошибка удаления")


@router.post("/tasks/web/create", response_class=HTMLResponse)
async def task_web_create(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    category_id: str = Form(""),
    category_name: str = Form(""),  # Новое поле для имени подкатегории
    priority: str = Form("средний"),
    due_date: str = Form(""),
    status: str = Form("новая"),
    is_milestone: bool = Form(False),
    impact_notes: str = Form(""),
):
    """Web: создать задачу из формы (form-data)"""
    async with async_session() as db:
        final_cat_id = None

        # 1. Если передан ID категории
        if category_id:
            final_cat_id = int(category_id)
        # 2. Если передано имя (например, "Созвоны" из модалки)
        elif category_name:
            # Ищем подкатегорию по имени
            cat_res = await db.execute(
                select(Category).where(Category.name == category_name)
            )
            cat = cat_res.scalar_one_or_none()
            if cat:
                final_cat_id = cat.id

        task = Task(
            title=title,
            description=description,
            category_id=final_cat_id,
            priority=priority,
            due_date=date.fromisoformat(due_date) if due_date else None,
            status=status,
            is_milestone=is_milestone,
            impact_notes=impact_notes,
            source="web",
        )
        db.add(task)
        await db.commit()

    # Редирект на дашборд
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/", status_code=303)


@router.post("/tasks/web/{task_id}/edit", response_class=HTMLResponse)
async def task_web_edit(
    request: Request,
    task_id: int,
    title: str = Form(...),
    description: str = Form(""),
    category_id: str = Form(""),
    priority: str = Form("средний"),
    due_date: str = Form(""),
    status: str = Form("новая"),
    is_milestone: bool = Form(False),
    impact_notes: str = Form(""),
):
    """Web: редактировать задачу из формы (form-data)"""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            return HTMLResponse(status_code=404, content="Задача не найдена")

        task.title = title
        task.description = description
        task.category_id = int(category_id) if category_id else None
        task.priority = priority
        task.due_date = date.fromisoformat(due_date) if due_date else None
        task.status = status
        task.is_milestone = is_milestone
        task.impact_notes = impact_notes
        
        if status == "выполнена" and not task.completed_at:
            task.completed_at = datetime.utcnow()
            task.is_archived = True
            task.item_kind = "task"
        elif status != "выполнена":
            task.completed_at = None
        await db.commit()

    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/", status_code=303)


# ---- Подзадачи (Subtasks) ----

@router.get("/tasks/{task_id}/subtasks", response_class=HTMLResponse)
async def get_subtasks_htmx(request: Request, task_id: int):
    """HTMX: загрузить список подзадач для родителя"""
    async with async_session() as db:
        result = await db.execute(
            select(Task).where(Task.parent_task_id == task_id).order_by(Task.created_at.asc())
        )
        subtasks = result.scalars().all()

    return templates.TemplateResponse(request, "partials/subtasks.html", {
        "request": request,
        "subtasks": subtasks,
        "parent_id": task_id,
    })


@router.post("/tasks/{task_id}/subtasks", response_class=HTMLResponse)
async def create_subtask_htmx(
    request: Request,
    task_id: int,
    title: str = Form(...),
):
    """HTMX: создать подзадачу"""
    async with async_session() as db:
        subtask = Task(
            title=title,
            parent_task_id=task_id,
            source="web",
            status="новая",
        )
        db.add(subtask)
        await db.commit()

        # Вернуть обновленный список
        result = await db.execute(
            select(Task).where(Task.parent_task_id == task_id).order_by(Task.created_at.asc())
        )
        subtasks = result.scalars().all()

    return templates.TemplateResponse(request, "partials/subtasks.html", {
        "request": request,
        "subtasks": subtasks,
        "parent_id": task_id,
    })


@router.get("/tasks/new", response_class=HTMLResponse)
async def task_form_page(request: Request):
    """Форма создания задачи"""
    categories = await get_categories_list()

    return templates.TemplateResponse(request, "task_form.html", {
        "request": request,
        "categories": categories,
        "task": None,
    })


@router.get("/tasks/{task_id}/edit", response_class=HTMLResponse)
async def task_edit_page(request: Request, task_id: int):
    """Форма редактирования задачи"""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=404, detail="Задача не найдена")

        categories = await get_categories_list()

    return templates.TemplateResponse(request, "task_form.html", {
        "request": request,
        "categories": categories,
        "task": task,
    })



@router.get("/tasks/list", response_class=HTMLResponse)
async def tasks_list_htmx(request: Request):
    """HTMX: список задач на сегодня (те же фильтры, что на дашборде)"""
    async with async_session() as db:
        return HTMLResponse(content=await get_tasks_today(db, request))


@router.post("/tasks/create", response_class=HTMLResponse)
async def task_create_htmx(
    request: Request,
    title: str = Form(...),
    category_id: str = Form(None),
):
    """HTMX: быстрое создание задачи на сегодня"""
    today = date.today()
    
    # Парсинг времени из заголовка (например, "12:00 Задача")
    due_time = None
    time_match = re.match(r'^(\d{1,2}:\d{2})\s+(.*)$', title)
    if time_match:
        try:
            time_str = time_match.group(1)
            title = time_match.group(2)
            # Приводим к формату ЧЧ:ММ (добавляем 0 если надо)
            if len(time_str.split(':')[0]) == 1:
                time_str = '0' + time_str
            due_time = time.fromisoformat(time_str)
        except ValueError:
            pass

    async with async_session() as db:
        # Автоматическая категоризация через AI, если категория не выбрана
        final_category_id = None
        if category_id and category_id.isdigit():
            final_category_id = int(category_id)
        else:
            # Пытаемся определить категорию через AI
            # Сначала получаем список всех категорий для контекста
            cat_stmt = select(Category).order_by(Category.is_global.desc(), Category.name)
            cat_res = await db.execute(cat_stmt)
            all_cats = [{"id": c.id, "name": c.name, "is_global": c.is_global} for c in cat_res.scalars().all()]
            
            ai_result = await ai_service.categorize(title, all_cats)
            if ai_result and ai_result.get("category_id"):
                final_category_id = int(ai_result["category_id"])

        task = Task(
            title=title,
            category_id=final_category_id,
            due_date=today,
            due_time=due_time,
            source="web",
            status="новая",
        )
        db.add(task)
        await db.commit()

        # Вернуть обновлённый список со всей нужной информацией
        result = await db.execute(
            select(Task)
            .options(selectinload(Task.category).selectinload(Category.parent))
            .where(
                Task.due_date == today,
                Task.is_archived == False,
                Task.parent_task_id == None
            ).order_by(Task.sort_order.asc(), Task.created_at.asc())
        )
        tasks = result.scalars().all()

        await repair_archived_subtasks(db)
        subtasks_map = await load_subtasks_map(db, [t.id for t in tasks])

    # Статистика для OOB
    completed, total = await get_today_stats(db)
    stats_oob = f'<span id="today-stats-counter" hx-swap-oob="true">{completed}/{total}</span>'

    # Отрисовка шаблона
    template = templates.get_template("partials/tasks_list.html")
    content = template.render({
        "request": request,
        "tasks": tasks,
        "subtasks_map": subtasks_map,
    })
    
    return HTMLResponse(content=content + stats_oob)


async def _complete_subtask_impl(db: AsyncSession, subtask: Task) -> None:
    """Закрыть подзадачу — остаётся в списке зачёркнутой."""
    subtask.status = "выполнена"
    subtask.completed_at = datetime.utcnow()
    subtask.is_archived = False


@router.post("/tasks/{task_id}/complete-subtask", response_class=HTMLResponse)
async def complete_subtask_htmx(request: Request, task_id: int):
    """HTMX: отметить подзадачу выполненной (strike-through, без архивации)."""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        subtask = result.scalar_one_or_none()
        if not subtask or not subtask.parent_task_id:
            raise HTTPException(status_code=404, detail="Subtask not found")

        await _complete_subtask_impl(db, subtask)
        await db.commit()

        row = templates.get_template("partials/subtask_row.html").render({
            "request": request,
            "sub": subtask,
            "parent_id": subtask.parent_task_id,
        })
        completed, total = await get_today_stats(db)
        stats_oob = f'<span id="today-stats-counter" hx-swap-oob="true">{completed}/{total}</span>'
        return HTMLResponse(content=row + stats_oob)


@router.post("/tasks/{task_id}/backlog", response_class=HTMLResponse)
async def task_to_backlog(request: Request, task_id: int):
    """Вернуть задачу в бэклог (убрать дату планирования)"""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.due_date = None
            task.status = "новая"
            await db.commit()
            return HTMLResponse(content=await get_tasks_today(db, request))
    raise HTTPException(status_code=404, detail="Задача не найдена")


@router.post("/tasks/{task_id}/complete", response_class=HTMLResponse)
async def complete_task(request: Request, task_id: int):
    """Отметить задачу выполненной"""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            if task.parent_task_id:
                await _complete_subtask_impl(db, task)
                await db.commit()
                row = templates.get_template("partials/subtask_row.html").render({
                    "request": request,
                    "sub": task,
                    "parent_id": task.parent_task_id,
                })
                completed, total = await get_today_stats(db)
                stats_oob = f'<span id="today-stats-counter" hx-swap-oob="true">{completed}/{total}</span>'
                return HTMLResponse(content=row + stats_oob)

            is_backlog = task.due_date is None
            task.status = "выполнена"
            task.completed_at = datetime.utcnow()
            if task.parent_task_id is None:
                task.is_archived = True
                task.item_kind = "task"
                children_result = await db.execute(
                    select(Task).where(
                        Task.parent_task_id == task.id,
                        Task.status != "выполнена",
                    )
                )
                for child in children_result.scalars().all():
                    child.status = "выполнена"
                    child.completed_at = datetime.utcnow()
                    child.is_archived = False
            await db.commit()

            target = request.headers.get("HX-Target", "")
            if target.startswith("task-") or is_backlog:
                return HTMLResponse(content="✅ выполнено")

            return HTMLResponse(content=await get_tasks_today(db, request))
    raise HTTPException(status_code=404, detail="Задача не найдена")


@router.delete("/tasks/{task_id}", response_class=HTMLResponse)
async def delete_task(request: Request, task_id: int):
    """Удалить задачу (soft delete)"""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.is_archived = True
            task.item_kind = "task"
            await db.commit()

            target = request.headers.get("HX-Target", "")
            if target.startswith("task-"):
                return HTMLResponse("")

            return HTMLResponse(content=await get_tasks_today(db, request))
    raise HTTPException(status_code=404, detail="Задача не найдена")


@router.post("/tasks/{task_id}/plan", response_class=HTMLResponse)
async def plan_task(request: Request, task_id: int, due_date: str = Form(None)):
    """Запланировать задачу на дату"""
    from datetime import date
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=404, detail="Задача не найдена")

        try:
            if due_date:
                day, month = due_date.split(".")
                task.due_date = date(date.today().year, int(month), int(day))
            else:
                task.due_date = date.today()
            task.status = "новая"
            await db.commit()

            target = request.headers.get("HX-Target", "")
            if target.startswith("task-"):
                return HTMLResponse(content=f"📅 {task.due_date.strftime('%d.%m')}")

            return HTMLResponse(content=await get_tasks_today(db, request))
        except:
            raise HTTPException(status_code=400, detail="Неверный формат даты (ДД.ММ)")
