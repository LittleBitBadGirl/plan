from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import async_session
from app.models.task import Task
from app.models.category import Category
from datetime import date, datetime
from pathlib import Path

router = APIRouter(tags=["web"])

# Шаблоны
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


async def get_categories_list():
    """Получить список категорий"""
    async with async_session() as db:
        result = await db.execute(
            select(Category).order_by(Category.is_global.desc(), Category.name)
        )
        return result.scalars().all()


# ---- Страницы ----

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Дашборд — задачи на сегодня"""
    today = date.today()
    async with async_session() as db:
        result = await db.execute(
            select(Task).where(
                Task.due_date == today,
                Task.is_archived == False,
                Task.status.in_(["новая", "в_работе"])
            ).order_by(Task.sort_order.asc())
        )
        tasks = result.scalars().all()

        # Статистика
        completed_result = await db.execute(
            select(func.count(Task.id)).where(
                Task.due_date == today,
                Task.status == "выполнена"
            )
        )
        completed = completed_result.scalar() or 0

        # Категории для формы
        cats_result = await db.execute(
            select(Category).where(Category.is_global == True).order_by(Category.name)
        )
        categories = cats_result.scalars().all()

        # AI предупреждение (заглушка)
        ai_warning = None
        if len(tasks) > 8:
            ai_warning = f"⚠️ Запланировано {len(tasks)} задач на сегодня. Обычно вы выполняете ~5."

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "tasks": tasks,
        "categories": categories,
        "completed": completed,
        "total": len(tasks) + completed,
        "today": today.isoformat(),
        "ai_warning": ai_warning,
    })


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request):
    """Все задачи"""
    async with async_session() as db:
        result = await db.execute(
            select(Task).where(
                Task.is_archived == False,
                Task.due_date != None
            ).order_by(Task.due_date.asc())
        )
        tasks = result.scalars().all()
        categories = await get_categories_list()

    return templates.TemplateResponse("tasks.html", {
        "request": request,
        "tasks": tasks,
        "categories": categories,
    })


@router.get("/backlog", response_class=HTMLResponse)
async def backlog_page(request: Request):
    """Бэклог — задачи без даты"""
    async with async_session() as db:
        result = await db.execute(
            select(Task).where(
                Task.is_archived == False,
                Task.due_date == None
            ).order_by(Task.created_at.desc())
        )
        tasks = result.scalars().all()

    return templates.TemplateResponse("backlog.html", {
        "request": request,
        "tasks": tasks,
    })


@router.get("/tasks/new", response_class=HTMLResponse)
async def task_form_page(request: Request):
    """Форма создания задачи"""
    categories = await get_categories_list()

    return templates.TemplateResponse("task_form.html", {
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

    return templates.TemplateResponse("task_form.html", {
        "request": request,
        "categories": categories,
        "task": task,
    })


@router.get("/categories", response_class=HTMLResponse)
async def categories_page(request: Request):
    """Управление категориями"""
    async with async_session() as db:
        result = await db.execute(
            select(Category).order_by(Category.is_global.desc(), Category.name)
        )
        categories = result.scalars().all()

        # Подсчитать задачи по категориям
        counts_result = await db.execute(
            select(Task.category_id, func.count(Task.id))
            .where(Task.is_archived == False)
            .group_by(Task.category_id)
        )
        task_counts = {row[0]: row[1] for row in counts_result.all()}

    # Сгруппировать по глобальным
    global_cats = [c for c in categories if c.is_global]
    sub_cats = {gc.id: [c for c in categories if c.parent_id == gc.id] for gc in global_cats}

    return templates.TemplateResponse("categories.html", {
        "request": request,
        "global_categories": global_cats,
        "sub_categories": sub_cats,
        "categories": categories,
        "task_counts": task_counts,
    })


@router.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request):
    """Календарь"""
    return templates.TemplateResponse("calendar.html", {
        "request": request,
        "today": date.today().isoformat(),
    })


@router.get("/archive", response_class=HTMLResponse)
async def archive_page(request: Request):
    """Архив"""
    async with async_session() as db:
        result = await db.execute(
            select(Task).where(Task.is_archived == True).order_by(Task.completed_at.desc()).limit(50)
        )
        tasks = result.scalars().all()

    return templates.TemplateResponse("archive.html", {
        "request": request,
        "tasks": tasks,
    })


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    """Статистика"""
    async with async_session() as db:
        result = await db.execute(
            select(
                func.count(Task.id).filter(Task.status == "выполнена").label("completed"),
                func.count(Task.id).filter(Task.is_archived == False).label("active"),
                func.count(Task.id).filter(Task.chronic_task == True).label("chronic"),
            )
        )
        row = result.first()

    return templates.TemplateResponse("stats.html", {
        "request": request,
        "completed": row.completed or 0,
        "active": row.active or 0,
        "chronic": row.chronic or 0,
    })


@router.get("/recurring", response_class=HTMLResponse)
async def recurring_page(request: Request):
    """Периодические задачи"""
    return templates.TemplateResponse("recurring.html", {"request": request})


# ---- HTMX эндпоинты ----

@router.get("/tasks/list", response_class=HTMLResponse)
async def tasks_list_htmx(request: Request):
    """HTMX: список задач на сегодня"""
    today = date.today()
    async with async_session() as db:
        result = await db.execute(
            select(Task).where(
                Task.due_date == today,
                Task.is_archived == False
            ).order_by(Task.sort_order.asc(), Task.created_at.asc())
        )
        tasks = result.scalars().all()

    return templates.TemplateResponse("partials/tasks_list.html", {
        "request": request,
        "tasks": tasks,
    })


@router.post("/tasks/create", response_class=HTMLResponse)
async def task_create_htmx(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    category_id: int = Form(None),
    priority: str = Form("средний"),
    due_date: str = Form(None),
):
    """HTMX: создать задачу"""
    async with async_session() as db:
        task = Task(
            title=title,
            description=description,
            category_id=category_id if category_id else None,
            priority=priority,
            due_date=date.fromisoformat(due_date) if due_date else date.today(),
            source="web",
        )
        db.add(task)
        await db.flush()
        await db.refresh(task)

        # Вернуть обновлённый список
        today = date.today()
        result = await db.execute(
            select(Task).where(
                Task.due_date == today,
                Task.is_archived == False
            ).order_by(Task.sort_order.asc(), Task.created_at.asc())
        )
        tasks = result.scalars().all()

    return templates.TemplateResponse("partials/tasks_list.html", {
        "request": request,
        "tasks": tasks,
    })


@router.post("/tasks/{task_id}/complete", response_class=HTMLResponse)
async def complete_task(task_id: int):
    """Отметить задачу выполненной"""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.status = "выполнена"
            task.completed_at = datetime.utcnow()
            await db.flush()
    return HTMLResponse(status_code=200)


@router.post("/tasks/{task_id}/backlog", response_class=HTMLResponse)
async def move_to_backlog(task_id: int):
    """Переместить задачу в бэклог (убрать дату)"""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.due_date = None
            task.status = "новая"
            await db.flush()
    return HTMLResponse(status_code=200)


@router.delete("/tasks/{task_id}", response_class=HTMLResponse)
async def delete_task(task_id: int):
    """Удалить задачу (soft delete → архив)"""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.is_archived = True
            await db.flush()
    return HTMLResponse(status_code=200)


@router.post("/tasks/{task_id}/plan", response_class=HTMLResponse)
async def plan_task(task_id: int):
    """Запланировать задачу на сегодня"""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.due_date = date.today()
            await db.flush()
    return HTMLResponse(status_code=200)


@router.post("/tasks/{task_id}/status", response_class=HTMLResponse)
async def task_status_htmx(
    request: Request,
    task_id: int,
    status: str = Form(...),
):
    """HTMX: изменить статус задачи"""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            return HTMLResponse(status_code=404, content="Задача не найдена")

        task.status = status
        if status == "выполнена":
            task.completed_at = datetime.utcnow()
        await db.flush()

        # Вернуть обновлённый список
        today = date.today()
        result = await db.execute(
            select(Task).where(
                Task.due_date == today,
                Task.is_archived == False
            ).order_by(Task.sort_order.asc(), Task.created_at.asc())
        )
        tasks = result.scalars().all()

    return templates.TemplateResponse("partials/tasks_list.html", {
        "request": request,
        "tasks": tasks,
    })
