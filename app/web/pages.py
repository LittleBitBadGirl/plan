from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import async_session
from app.models.task import Task
from app.models.category import Category
from app.models.recurring import RecurringTask
from datetime import date, datetime
from pathlib import Path
import json
from typing import List
from app.services.rollover_service import rollover_overdue_tasks

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
    from app.models.recurring import RecurringTask
    today = date.today()

    # Автоматический перенос просроченных задач при открытии дашборда
    rollover_result = await rollover_overdue_tasks()
    if rollover_result["moved"] > 0:
        from app.utils.logger import app_logger
        app_logger.info(f"🔄 Auto-rollover: перенесено {rollover_result['moved']} задач на сегодня")

    async with async_session() as db:
        # Обычные задачи (только корневые)
        result = await db.execute(
            select(Task)
            .options(selectinload(Task.category).selectinload(Category.parent))
            .where(
                Task.due_date == today,
                Task.is_archived == False,
                Task.status.in_(["новая", "в_работе"]),
                Task.parent_task_id == None  # Только корневые задачи
            ).order_by(Task.sort_order.asc())
        )
        tasks = list(result.scalars().all())

        # Загружаем подзадачи отдельно и создаем словарь {task_id: [subtasks]}
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

        # Периодические задачи на сегодня
        recur_result = await db.execute(
            select(RecurringTask).where(RecurringTask.is_active == True)
        )
        all_recurring = recur_result.scalars().all()

        day_of_week = today.weekday()
        day_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        recurring_today = []

        for rt in all_recurring:
            if rt.end_date and today > rt.end_date:
                continue
            if today < rt.start_date:
                continue

            if rt.recurrence_type == "daily":
                recurring_today.append(rt)
            elif rt.recurrence_type == "weekly":
                if rt.recurrence_days:
                    days = rt.recurrence_days
                    if isinstance(days, str):
                        import json
                        try:
                            days = json.loads(days)
                        except Exception:
                            days = []
                    if day_names[day_of_week] in days:
                        recurring_today.append(rt)
            elif rt.recurrence_type == "monthly":
                if today.day == rt.start_date.day:
                    recurring_today.append(rt)

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
        "subtasks_map": subtasks_map,  # Передаем словарь подзадач
        "recurring_tasks": recurring_today,
        "categories": categories,
        "completed": completed,
        "total": len(tasks) + completed,
        "today": today,
        "ai_warning": ai_warning,
    })


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request):
    """Все задачи"""
    async with async_session() as db:
        result = await db.execute(
            select(Task)
            .options(selectinload(Task.category))
            .where(
                Task.is_archived == False,
                Task.due_date != None
            )
            .order_by(Task.due_date.asc())
        )
        tasks = result.scalars().all()
        categories = await get_categories_list()

    return templates.TemplateResponse("tasks.html", {
        "request": request,
        "tasks": tasks,
        "categories": categories,
        "total": len(tasks),
        "status_filter": None,
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

            # Возвращаем обновленный список подзадач
            subtasks_result = await db.execute(
                select(Task).where(Task.parent_task_id == parent_id, Task.is_archived == False)
            )
            subtasks = subtasks_result.scalars().all()

            return templates.TemplateResponse("partials/subtasks.html", {
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
        if status == "выполнена" and not task.completed_at:
            task.completed_at = datetime.utcnow()
        elif status != "выполнена":
            task.completed_at = None
        await db.commit()

    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/", status_code=303)


@router.get("/backlog", response_class=HTMLResponse)
async def backlog_page(request: Request):
    """Бэклог — задачи без даты"""
    async with async_session() as db:
        result = await db.execute(
            select(Task)
            .options(selectinload(Task.category))
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

    return templates.TemplateResponse("backlog.html", {
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

        # 2. Создаем RecurringTask
        recurring = RecurringTask(
            title=task.title,
            description=task.description,
            category_id=task.category_id,
            priority=task.priority,
            recurrence_type=recurrence_type,
            recurrence_days=json.dumps(recurrence_days) if recurrence_type == "weekly" and recurrence_days else None,
            start_date=date.today(),
            is_active=True,
        )
        db.add(recurring)

        # 3. Удаляем старую задачу из бэклога
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


# ---- Подзадачи (Subtasks) ----

@router.get("/tasks/{task_id}/subtasks", response_class=HTMLResponse)
async def get_subtasks_htmx(request: Request, task_id: int):
    """HTMX: загрузить список подзадач для родителя"""
    async with async_session() as db:
        result = await db.execute(
            select(Task).where(Task.parent_task_id == task_id, Task.is_archived == False)
        )
        subtasks = result.scalars().all()

    return templates.TemplateResponse("partials/subtasks.html", {
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
            select(Task).where(Task.parent_task_id == task_id, Task.is_archived == False)
        )
        subtasks = result.scalars().all()

    return templates.TemplateResponse("partials/subtasks.html", {
        "request": request,
        "subtasks": subtasks,
        "parent_id": task_id,
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


@router.post("/categories/create", response_class=HTMLResponse)
async def create_category_from_form(
    request: Request,
    name: str = Form(...),
    parent_id: str = Form(""),
):
    """Создать категорию из формы веб-интерфейса"""
    async with async_session() as db:
        # Если выбран родитель, создаем подкатегорию
        final_parent_id = int(parent_id) if parent_id else None
        is_global = final_parent_id is None

        category = Category(
            name=name,
            is_global=is_global,
            parent_id=final_parent_id,
        )
        db.add(category)
        await db.commit()

    # Перенаправляем обратно на страницу категорий
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/categories", status_code=303)


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


@router.post("/archive/{task_id}/restore", response_class=HTMLResponse)
async def restore_task(task_id: int):
    """Восстановить задачу из архива"""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.is_archived = False
            task.status = "новая"
            task.completed_at = None
            await db.commit()
            # Возвращаем пустой блок, чтобы HTMX удалил строку из таблицы
            return HTMLResponse(f'<div id="archive-task-{task_id}" class="hidden"></div>')
    return HTMLResponse('<div class="text-red-400 p-4">Ошибка восстановления</div>')


@router.get("/archive", response_class=HTMLResponse)
async def archive_page(request: Request, page: int = 1, limit: int = 50):
    """Архив — выполненные и удалённые задачи с пагинацией"""
    offset = (page - 1) * limit

    async with async_session() as db:
        # Общее количество
        total_result = await db.execute(
            select(func.count(Task.id)).where(Task.is_archived == True)
        )
        total = total_result.scalar() or 0

        # Задачи для текущей страницы
        result = await db.execute(
            select(Task)
            .options(selectinload(Task.category))
            .where(Task.is_archived == True)
            .order_by(Task.completed_at.desc())
            .offset(offset)
            .limit(limit)
        )
        tasks = list(result.scalars().all())

        has_next = offset + limit < total
        has_prev = page > 1

    return templates.TemplateResponse("archive.html", {
        "request": request,
        "tasks": tasks,
        "total": total,
        "page": page,
        "limit": limit,
        "has_prev": has_prev,
        "has_next": has_next,
        "prev_page": page - 1,
        "next_page": page + 1,
    })


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    """Статистика"""
    async with async_session() as db:
        root_filter = Task.parent_task_id == None

        completed_result = await db.execute(
            select(func.count(Task.id)).where(Task.status == "выполнена", root_filter)
        )
        total_completed = completed_result.scalar() or 0

        active_result = await db.execute(
            select(func.count(Task.id)).where(Task.is_archived == False, Task.status != "выполнена", root_filter)
        )
        total_active = active_result.scalar() or 0

        new_result = await db.execute(
            select(func.count(Task.id)).where(Task.status == "новая", Task.is_archived == False, root_filter)
        )
        total_new = new_result.scalar() or 0

        # 1. Задачи без категории (активные)
        no_cat_result = await db.execute(
            select(func.count(Task.id)).where(
                Task.category_id == None,
                Task.is_archived == False,
                root_filter
            )
        )
        no_category_count = no_cat_result.scalar() or 0

        # 2. Средняя скорость выполнения (в днях)
        # Используем julianday для SQLite: разница дат в днях
        speed_result = await db.execute(
            select(func.avg(func.julianday(Task.completed_at) - func.julianday(Task.created_at))).where(
                Task.status == "выполнена",
                Task.completed_at != None,
                root_filter
            )
        )
        avg_speed_days = speed_result.scalar()
        avg_speed = f"{avg_speed_days:.1f} дн." if avg_speed_days else "—"

    return templates.TemplateResponse("stats.html", {
        "request": request,
        "total_completed": total_completed,
        "total_active": total_active,
        "total_new": total_new,
        "no_category_count": no_category_count,
        "avg_speed": avg_speed,
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
    """Отметить задачу выполненной → перемещает в архив"""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.status = "выполнена"
            task.completed_at = datetime.utcnow()
            task.is_archived = True
            await db.commit()
            return HTMLResponse(f'<div class="text-green-400 p-4">✅ {task.title} — выполнено! (в архиве)</div>')
    return HTMLResponse('<div class="text-gray-500 p-4">Задача не найдена</div>')


@router.post("/tasks/{task_id}/backlog", response_class=HTMLResponse)
async def move_to_backlog(task_id: int):
    """Переместить задачу в бэклог (убрать дату)"""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.due_date = None
            task.status = "новая"
            await db.commit()
            return HTMLResponse(f'<div class="text-yellow-400 p-4">📥 {task.title} → в бэклог</div>')
    return HTMLResponse('<div class="text-gray-500 p-4">Задача не найдена</div>')


@router.delete("/tasks/{task_id}", response_class=HTMLResponse)
async def delete_task(task_id: int):
    """Удалить задачу (soft delete → архив)"""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.is_archived = True
            await db.commit()
            return HTMLResponse(f"""
                <div hx-swap-oob="delete:#task-{task_id}"></div>
                <div id="toast" class="fixed top-4 right-4 bg-red-600 text-white px-4 py-2 rounded shadow-lg z-50 animate-fade-in">
                    🗑 {task.title} — в архив
                </div>
            """)
    return HTMLResponse('<div class="text-gray-500 p-4">Задача не найдена</div>')


@router.post("/tasks/{task_id}/plan", response_class=HTMLResponse)
async def plan_task(task_id: int, due_date: str = Form(None)):
    """Запланировать задачу на дату"""
    from datetime import date
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            if due_date:
                # Парсим ДД.ММ (год текущий)
                try:
                    day, month = due_date.split(".")
                    task.due_date = date(date.today().year, int(month), int(day))
                    await db.flush()
                    await db.commit()
                    return HTMLResponse(content=f"""
                        <div hx-swap-oob="delete:#task-{task_id}"></div>
                        <div id="toast" class="fixed top-4 right-4 bg-blue-600 text-white px-4 py-2 rounded shadow-lg z-50 animate-fade-in">
                            📅 {task.title} → {due_date}.{date.today().year}
                        </div>
                    """)
                except (ValueError, IndexError) as e:
                    return HTMLResponse(content=f'<div class="text-red-400 p-4">❌ Неверный формат: {e}. Введите ДД.ММ</div>', status_code=400)
            else:
                task.due_date = date.today()
                await db.flush()
                await db.commit()
                return HTMLResponse(content=f"""
                    <div hx-swap-oob="delete:#task-{task_id}"></div>
                    <div id="toast" class="fixed top-4 right-4 bg-blue-600 text-white px-4 py-2 rounded shadow-lg z-50 animate-fade-in">
                        📅 {task.title} → сегодня
                    </div>
                """)
    return HTMLResponse(content='<div class="text-gray-500 p-4">Задача не найдена</div>', status_code=404)


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
