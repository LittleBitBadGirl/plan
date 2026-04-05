from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import date, datetime
from typing import Optional
from pathlib import Path
import httpx

from app.config import settings
from app.db.database import get_db
from app.models.task import Task
from app.models.category import Category

router = APIRouter()

# Шаблоны
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

# Базовый URL API
API_BASE = "http://127.0.0.1:8000"


async def get_api_client():
    """Создать HTTP-клиент для запросов к API"""
    client = httpx.AsyncClient(base_url=API_BASE, timeout=10.0)
    client.headers.update({"X-API-Token": settings.api_token})
    return client


def check_auth(request: Request) -> bool:
    """Проверить аутентификацию по сессии"""
    return request.session.get("authenticated", False)


def require_auth(request: Request):
    """Редирект на логин если не авторизован"""
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=303)
    return None


# ---- Страницы ----

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """Дашборд: задачи на сегодня + статистика"""
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect

    today = date.today()

    # Задачи на сегодня
    today_tasks = await db.execute(
        select(Task).where(
            Task.due_date == today,
            Task.is_archived == False
        ).order_by(Task.sort_order.asc(), Task.created_at.asc())
    )
    today_tasks = today_tasks.scalars().all()

    # Статистика
    completed_result = await db.execute(
        select(func.count(Task.id)).where(
            Task.status == "выполнена",
            Task.completed_at >= today
        )
    )
    today_completed = completed_result.scalar() or 0

    total_today_result = await db.execute(
        select(func.count(Task.id)).where(
            Task.due_date == today,
            Task.is_archived == False
        )
    )
    total_today = total_today_result.scalar() or 0

    # Категории для формы
    categories_result = await db.execute(
        select(Category).where(Category.is_global == True).order_by(Category.name)
    )
    categories = categories_result.scalars().all()

    # AI предупреждение (простая логика)
    ai_warning = None
    if total_today > 8:
        ai_warning = f"⚠️ Перегрузка: запланировано {total_today} задач. Обычно вы выполняете 5-7 задач/день."

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "tasks": today_tasks,
        "categories": categories,
        "today_completed": today_completed,
        "total_today": total_today,
        "ai_warning": ai_warning,
        "today": today.isoformat(),
    })


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Страница входа"""
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, token: str = Form(...)):
    """Обработка входа по токену"""
    if token == settings.api_token:
        request.session["authenticated"] = True
        return RedirectResponse(url="/", status_code=303)
    else:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Неверный токен"
        })


@router.get("/logout")
async def logout(request: Request):
    """Выход"""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status: Optional[str] = None,
    category_id: Optional[int] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """Все задачи с фильтрами"""
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect

    query = select(Task).where(Task.is_archived == False)

    if status:
        query = query.where(Task.status == status)
    if category_id:
        query = query.where(Task.category_id == category_id)
    if from_date:
        query = query.where(Task.due_date >= date.fromisoformat(from_date))
    if to_date:
        query = query.where(Task.due_date <= date.fromisoformat(to_date))

    # Счётчик для пагинации
    count_query = select(func.count(Task.id)).where(Task.is_archived == False)
    if status:
        count_query = count_query.where(Task.status == status)
    if category_id:
        count_query = count_query.where(Task.category_id == category_id)
    if from_date:
        count_query = count_query.where(Task.due_date >= date.fromisoformat(from_date))
    if to_date:
        count_query = count_query.where(Task.due_date <= date.fromisoformat(to_date))

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(Task.due_date.asc(), Task.sort_order.asc())
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    tasks = result.scalars().all()

    # Категории для фильтров
    categories_result = await db.execute(
        select(Category).where(Category.is_global == True).order_by(Category.name)
    )
    categories = categories_result.scalars().all()

    return templates.TemplateResponse("tasks.html", {
        "request": request,
        "tasks": tasks,
        "categories": categories,
        "total": total,
        "limit": limit,
        "offset": offset,
        "status_filter": status,
        "category_id_filter": category_id,
        "from_date_filter": from_date,
        "to_date_filter": to_date,
        "has_prev": offset > 0,
        "has_next": offset + limit < total,
        "prev_offset": max(0, offset - limit),
        "next_offset": offset + limit,
    })


@router.get("/tasks/new", response_class=HTMLResponse)
async def task_form_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Форма создания задачи"""
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect

    categories_result = await db.execute(
        select(Category).order_by(Category.is_global.desc(), Category.name)
    )
    categories = categories_result.scalars().all()

    return templates.TemplateResponse("task_form.html", {
        "request": request,
        "categories": categories,
        "task": None,
    })


@router.get("/tasks/{task_id}/edit", response_class=HTMLResponse)
async def task_edit_page(request: Request, task_id: int, db: AsyncSession = Depends(get_db)):
    """Форма редактирования задачи"""
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect

    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    categories_result = await db.execute(
        select(Category).order_by(Category.is_global.desc(), Category.name)
    )
    categories = categories_result.scalars().all()

    return templates.TemplateResponse("task_form.html", {
        "request": request,
        "categories": categories,
        "task": task,
    })


@router.get("/categories", response_class=HTMLResponse)
async def categories_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Управление категориями"""
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect

    result = await db.execute(
        select(Category).order_by(Category.is_global.desc(), Category.name)
    )
    categories = result.scalars().all()

    return templates.TemplateResponse("categories.html", {
        "request": request,
        "categories": categories,
    })


@router.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Календарь задач"""
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect

    return templates.TemplateResponse("calendar.html", {
        "request": request,
        "today": date.today().isoformat(),
    })


@router.get("/archive", response_class=HTMLResponse)
async def archive_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
):
    """Архив задач"""
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect

    query = (
        select(Task)
        .where(Task.is_archived == True)
        .order_by(Task.completed_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(query)
    tasks = result.scalars().all()

    count_result = await db.execute(
        select(func.count(Task.id)).where(Task.is_archived == True)
    )
    total = count_result.scalar() or 0

    return templates.TemplateResponse("archive.html", {
        "request": request,
        "tasks": tasks,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_prev": offset > 0,
        "has_next": offset + limit < total,
        "prev_offset": max(0, offset - limit),
        "next_offset": offset + limit,
    })


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Статистика"""
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect

    # Общая статистика
    total_result = await db.execute(
        select(func.count(Task.id)).where(Task.is_archived == False)
    )
    total_active = total_result.scalar() or 0

    completed_result = await db.execute(
        select(func.count(Task.id)).where(Task.status == "выполнена")
    )
    total_completed = completed_result.scalar() or 0

    new_result = await db.execute(
        select(func.count(Task.id)).where(Task.status == "новая", Task.is_archived == False)
    )
    total_new = new_result.scalar() or 0

    return templates.TemplateResponse("stats.html", {
        "request": request,
        "total_active": total_active,
        "total_completed": total_completed,
        "total_new": total_new,
    })


@router.get("/recurring", response_class=HTMLResponse)
async def recurring_page(request: Request):
    """Периодические задачи"""
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect

    return templates.TemplateResponse("recurring.html", {
        "request": request,
    })


# ---- HTMX эндпоинты ----

@router.get("/tasks/list", response_class=HTMLResponse)
async def tasks_list_htmx(request: Request, db: AsyncSession = Depends(get_db)):
    """HTMX: список задач на сегодня"""
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


@router.post("/tasks/create", response_class=HTMLResponse)
async def task_create_htmx(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    category_id: Optional[int] = Form(None),
    priority: str = Form("средний"),
    due_date: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """HTMX: создать задачу"""
    task = Task(
        title=title,
        description=description,
        category_id=category_id,
        priority=priority,
        due_date=date.fromisoformat(due_date) if due_date else date.today(),
        source="web",
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)

    # Вернуть обновлённый список
    return await tasks_list_htmx(request, db)


@router.post("/tasks/{task_id}/complete", response_class=HTMLResponse)
async def task_complete_htmx(
    request: Request,
    task_id: int,
    db: AsyncSession = Depends(get_db),
):
    """HTMX: отметить задачу выполненной"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        return HTMLResponse(status_code=404, content="Задача не найдена")

    task.status = "выполнена"
    task.completed_at = datetime.utcnow()
    await db.flush()

    return await tasks_list_htmx(request, db)


@router.post("/tasks/{task_id}/delete", response_class=HTMLResponse)
async def task_delete_htmx(
    request: Request,
    task_id: int,
    db: AsyncSession = Depends(get_db),
):
    """HTMX: удалить задачу (soft)"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        return HTMLResponse(status_code=404, content="Задача не найдена")

    task.is_archived = True
    await db.flush()

    return await tasks_list_htmx(request, db)


@router.post("/tasks/{task_id}/status", response_class=HTMLResponse)
async def task_status_htmx(
    request: Request,
    task_id: int,
    status: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """HTMX: изменить статус задачи"""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        return HTMLResponse(status_code=404, content="Задача не найдена")

    task.status = status
    if status == "выполнена":
        task.completed_at = datetime.utcnow()
    await db.flush()

    return await tasks_list_htmx(request, db)
