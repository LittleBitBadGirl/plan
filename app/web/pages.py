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
from app.models.shopping import ShoppingItem
from app.models.report import AIReport
from datetime import date, datetime, time, timedelta
from pathlib import Path
import json
from typing import List
from app.services.rollover_service import rollover_overdue_tasks

import re

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


async def get_today_stats(db: AsyncSession):
    """Вспомогательная функция для получения статистики на сегодня"""
    today = date.today()
    # Считаем только корневые задачи
    completed_result = await db.execute(
        select(func.count(Task.id)).where(
            Task.due_date == today,
            Task.status == "выполнена",
            Task.parent_task_id == None
        )
    )
    completed = completed_result.scalar() or 0
    
    total_result = await db.execute(
        select(func.count(Task.id)).where(
            Task.due_date == today,
            Task.parent_task_id == None,
            (Task.is_archived == False) | (Task.status == "выполнена")
        )
    )
    total = total_result.scalar() or 0
    return completed, total


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
            
            # Логи для этой конкретной привычки
            h_logs_result = await db.execute(
                select(HabitLog.date).where(HabitLog.habit_id == h.id)
            )
            h_logs = {log_date.isoformat() for log_date in h_logs_result.scalars().all()}
            
            habits_data.append({
                "habit": h,
                "dates": h_dates,
                "logs": h_logs,
                "progress": len(h_logs)
            })

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

        # Находим названия всех задач на сегодня (и активных, и в архиве/выполненных)
        # Это нужно, чтобы не дублировать периодические задачи, которые уже созданы или выполнены
        today_titles_result = await db.execute(
            select(Task.title).where(Task.due_date == today)
        )
        all_occupied_titles = set(today_titles_result.scalars().all())

        day_of_week = today.weekday()
        day_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        recurring_today = []

        for rt in all_recurring:
            # Если задача с таким названием уже существует на сегодня — пропускаем
            if rt.title in all_occupied_titles:
                continue

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

        # Получаем статистику через хелпер
        completed, total = await get_today_stats(db)

        # Категории для формы (загружаем все для выпадающего списка)
        cats_result = await db.execute(
            select(Category).order_by(Category.is_global.desc(), Category.name)
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
        "total": total,
        "today": today,
        "ai_warning": ai_warning,
        "habits_data": habits_data,
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


@router.get("/categories/{category_id}/edit-form", response_class=HTMLResponse)
async def get_category_edit_form(category_id: int):
    """Вернуть инлайн-форму редактирования названия категории"""
    async with async_session() as db:
        result = await db.execute(select(Category).where(Category.id == category_id))
        category = result.scalar_one_or_none()
        if not category:
            return HTMLResponse("Ошибка")
        
    return HTMLResponse(f"""
        <form hx-post="/categories/{category_id}/edit" hx-swap="outerHTML" class="flex gap-2 items-center">
            <input type="text" name="name" value="{category.name}" 
                   class="bg-dark-900 border border-accent rounded px-2 py-1 text-sm text-white focus:outline-none w-full">
            <button type="submit" class="text-green-400 text-xs">✅</button>
            <button type="button" onclick="window.location.reload()" class="text-gray-500 text-xs">❌</button>
        </form>
    """)


@router.post("/categories/{parent_id}/sub/create", response_class=HTMLResponse)
async def create_subcategory_inline(parent_id: int, name: str = Form(...)):
    """Создать подкатегорию прямо из карточки родителя"""
    async with async_session() as db:
        new_sub = Category(name=name, parent_id=parent_id, is_global=False)
        db.add(new_sub)
        await db.commit()
    
    # Перезагружаем всю страницу, чтобы обновить счетчики и списки (самый надежный способ)
    from fastapi.responses import RedirectResponse
    return HTMLResponse(content='<script>window.location.reload()</script>')


@router.delete("/api/categories/{category_id}", response_class=HTMLResponse)
async def delete_category_htmx(category_id: int):
    """Удалить категорию (HTMX)"""
    async with async_session() as db:
        result = await db.execute(select(Category).where(Category.id == category_id))
        cat = result.scalar_one_or_none()
        if cat:
            await db.delete(cat)
            await db.commit()
            return HTMLResponse(content="") # Удалит элемент из DOM
    return HTMLResponse(status_code=404)


@router.post("/categories/{category_id}/edit", response_class=HTMLResponse)
async def edit_category(category_id: int, name: str = Form(...)):
    """Сохранить новое название категории"""
    async with async_session() as db:
        result = await db.execute(select(Category).where(Category.id == category_id))
        category = result.scalar_one_or_none()
        if category:
            category.name = name
            await db.commit()
            
            # Возвращаем заголовок/элемент с новым именем
            # Если это глобальная категория, возвращаем h2, если подкатегория - span
            if category.is_global:
                return HTMLResponse(f"""
                    <h2 class="text-lg font-bold text-white flex items-center gap-2" id="cat-title-{category_id}">
                        <span class="text-accent text-xl">🏷️</span>
                        {category.name}
                    </h2>
                """)
            else:
                return HTMLResponse(f"""
                    <div class="flex items-center justify-between group/item p-2 hover:bg-dark-700/30 rounded transition-colors" id="cat-title-{category_id}">
                        <span class="text-sm text-gray-300 flex items-center gap-2">
                            <span class="text-gray-600">└</span>
                            {category.name}
                        </span>
                        <div class="flex items-center gap-2 opacity-0 group-hover/item:opacity-100 transition-opacity">
                            <button hx-get="/categories/{category_id}/edit-form" hx-target="#cat-title-{category_id}" class="text-xs text-gray-500 hover:text-white" title="Изменить">✏️</button>
                            <button hx-delete="/api/categories/{category_id}" hx-target="#cat-title-{category_id}" hx-swap="delete" hx-confirm="Удалить подкатегорию?" class="text-red-500 hover:text-red-400 text-xs" title="Удалить">🗑</button>
                        </div>
                    </div>
                """)
    return HTMLResponse("Ошибка")


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

        # 1. Задачи без категории
        no_cat_result = await db.execute(
            select(func.count(Task.id)).where(
                Task.category_id == None,
                Task.is_archived == False,
                root_filter
            )
        )
        no_category_count = no_cat_result.scalar() or 0

        # 2. Средняя скорость выполнения
        speed_result = await db.execute(
            select(func.avg(func.julianday(Task.completed_at) - func.julianday(Task.created_at))).where(
                Task.status == "выполнена",
                Task.completed_at != None,
                root_filter
            )
        )
        avg_speed_days = speed_result.scalar()
        avg_speed = f"{avg_speed_days:.1f} дн." if avg_speed_days else "—"

        # 3. Распределение по категориям (Топ-5)
        cat_stats_query = (
            select(Category.name, func.count(Task.id))
            .join(Task, Task.category_id == Category.id)
            .where(Task.status == "выполнена", root_filter)
            .group_by(Category.name)
            .order_by(func.count(Task.id).desc())
            .limit(5)
        )
        cat_stats_result = await db.execute(cat_stats_query)
        category_distribution = cat_stats_result.all()

        # 4. Прогресс за последние 7 дней
        from datetime import timedelta
        week_ago = date.today() - timedelta(days=7)
        weekly_stats_query = (
            select(func.date(Task.completed_at), func.count(Task.id))
            .where(Task.completed_at >= week_ago, Task.status == "выполнена", root_filter)
            .group_by(func.date(Task.completed_at))
            .order_by(func.date(Task.completed_at).asc())
        )
        weekly_result = await db.execute(weekly_stats_query)
        weekly_history = weekly_result.all()

        # Последний AI отчет
        report_result = await db.execute(
            select(AIReport).order_by(AIReport.report_date.desc())
        )
        last_report = report_result.scalars().first()

    return templates.TemplateResponse("stats.html", {
        "request": request,
        "total_completed": total_completed,
        "total_active": total_active,
        "total_new": total_new,
        "no_category_count": no_category_count,
        "avg_speed": avg_speed,
        "category_distribution": category_distribution,
        "weekly_history": weekly_history,
        "last_report": last_report,
    })


@router.get("/api/ai/prepare-analysis", response_class=HTMLResponse)
async def prepare_analysis_data(request: Request):
    """Подготовить текстовый дамп задач за вчера для Gemini"""
    yesterday = date.today() - timedelta(days=1)
    
    async with async_session() as db:
        # Задачи за вчера
        result = await db.execute(
            select(Task)
            .options(selectinload(Task.category))
            .where(
                (func.date(Task.completed_at) == yesterday) | 
                (Task.due_date == yesterday)
            )
        )
        tasks = result.scalars().all()

        if not tasks:
            return HTMLResponse(f"""
                <div class="bg-yellow-900/20 border border-yellow-700/50 p-6 rounded-xl text-center">
                    <p class="text-yellow-500">За вчера ({yesterday.strftime('%d.%m')}) не найдено задач в плане.</p>
                </div>
            """)

        # Формируем отчет для терминала
        summary = f"Данные за {yesterday.strftime('%d.%m.%Y')} готовы.\n"
        summary += f"Всего задействовано задач: {len(tasks)}\n\n"

        for t in tasks:
            status_icon = "✅" if t.status == "выполнена" else "❌"
            cat = t.category.name if t.category else "Без категории"
            summary += f"{status_icon} [{cat}] {t.title}\n"

        return HTMLResponse(f"""
        <div class="bg-blue-900/20 border border-blue-500/50 p-6 rounded-xl">
            <h3 class="text-blue-400 font-bold mb-3 flex items-center gap-2">
                <span>🤖</span> Инструкция для Gemini
            </h3>
            <p class="text-gray-300 text-sm mb-4">
                Данные за вчерашний день успешно выгружены. Теперь просто напишите в терминале:
            </p>
            <div class="bg-dark-900 p-4 rounded border border-dark-600 font-mono text-xs text-green-400 mb-4 select-all">
                Gemini, проанализируй вчерашний день ({yesterday.strftime('%d.%m')}) и сохрани отчет в базу.
            </div>
            <p class="text-gray-500 text-[10px]">
                Я увижу эти данные в базе и напишу Senior-разбор прямо здесь на странице.
            </p>
        </div>
        """)

@router.get("/recurring", response_class=HTMLResponse)
async def recurring_page(request: Request):
    """Периодические задачи — управление шаблонами"""
    async with async_session() as db:
        # Получаем все шаблоны
        result = await db.execute(
            select(RecurringTask)
            .options(selectinload(RecurringTask.category))
            .order_by(RecurringTask.is_active.desc(), RecurringTask.title)
        )
        recurring_tasks = result.scalars().all()

        # Категории для формы (загружаем все)
        cats_result = await db.execute(
            select(Category).order_by(Category.is_global.desc(), Category.name)
        )
        categories = cats_result.scalars().all()

    return templates.TemplateResponse("recurring.html", {
        "request": request,
        "recurring_tasks": recurring_tasks,
        "categories": categories,
    })


@router.post("/api/recurring/web-create", response_class=HTMLResponse)
async def create_recurring_web(
    request: Request,
    title: str = Form(...),
    category_id: str = Form(None),
    recurrence_type: str = Form(...),
    days: List[str] = Form(None),
    start_date: str = Form(None),
):
    """Создать периодическую задачу из веб-интерфейса"""
    async with async_session() as db:
        # Проверка на дубликат (title + recurrence_type)
        existing = await db.execute(
            select(RecurringTask).where(
                RecurringTask.title == title,
                RecurringTask.recurrence_type == recurrence_type,
            )
        )
        if existing.scalar_one_or_none():
            return HTMLResponse(content='<script>alert("Ошибка: Такой шаблон уже существует!"); window.history.back();</script>')

        new_rt = RecurringTask(
            title=title,
            category_id=int(category_id) if category_id and category_id.isdigit() else None,
            recurrence_type=recurrence_type,
            recurrence_days=days if recurrence_type == "weekly" and days else None,
            start_date=date.fromisoformat(start_date) if start_date else date.today(),
            is_active=True,
        )
        db.add(new_rt)
        await db.commit()

    # Просто перезагружаем страницу
    return HTMLResponse(content='<script>window.location.reload()</script>')

# ---- HTMX эндпоинты ----

@router.get("/tasks/list", response_class=HTMLResponse)
async def tasks_list_htmx(request: Request):
    """HTMX: список задач на сегодня"""
    today = date.today()
    async with async_session() as db:
        result = await db.execute(
            select(Task)
            .options(selectinload(Task.category))
            .where(
                Task.due_date == today,
                Task.is_archived == False,
                Task.parent_task_id == None
            ).order_by(Task.sort_order.asc(), Task.created_at.asc())
        )
        tasks = list(result.scalars().all())

        # Загружаем подзадачи
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

    return templates.TemplateResponse("partials/tasks_list.html", {
        "request": request,
        "tasks": tasks,
        "subtasks_map": subtasks_map,
    })


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
        task = Task(
            title=title,
            category_id=int(category_id) if category_id and category_id.isdigit() else None,
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

        # Загружаем подзадачи
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


async def get_tasks_today(db: AsyncSession, request: Request):
    """Вспомогательная функция для получения списка задач на сегодня и их отрисовки"""
    today = date.today()
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


@router.post("/tasks/{task_id}/complete", response_class=HTMLResponse)
async def complete_task(request: Request, task_id: int):
    """Отметить задачу выполненной → перемещает в архив"""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.status = "выполнена"
            task.completed_at = datetime.utcnow()
            task.is_archived = True
            await db.commit()

            tasks_content = await get_tasks_today(db, request)
            toast_oob = f"""
                <div id="toast" hx-swap-oob="afterbegin:body" class="fixed top-4 right-4 bg-success text-white px-4 py-2 rounded shadow-lg z-50 animate-fade-in">
                    ✅ {task.title} — выполнено
                </div>
            """
            return HTMLResponse(content=tasks_content + toast_oob)
    return HTMLResponse('<div class="text-gray-500 p-4">Задача не найдена</div>')


@router.post("/tasks/{task_id}/backlog", response_class=HTMLResponse)
async def move_to_backlog(request: Request, task_id: int):
    """Переместить задачу в бэклог (убрать дату)"""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.due_date = None
            task.status = "новая"
            await db.commit()
            return HTMLResponse(content=await get_tasks_today(db, request))
    return HTMLResponse('<div class="text-gray-500 p-4">Задача не найдена</div>')


@router.delete("/tasks/{task_id}", response_class=HTMLResponse)
async def delete_task(request: Request, task_id: int):
    """Удалить задачу (soft delete → архив)"""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.is_archived = True
            await db.commit()
            
            tasks_content = await get_tasks_today(db, request)
            toast_oob = f"""
                <div id="toast" hx-swap-oob="afterbegin:body" class="fixed top-4 right-4 bg-red-600 text-white px-4 py-2 rounded shadow-lg z-50 animate-fade-in">
                    🗑 {task.title} — в архив
                </div>
            """
            return HTMLResponse(content=tasks_content + toast_oob)
    return HTMLResponse('<div class="text-gray-500 p-4">Задача не найдена</div>')


@router.post("/tasks/{task_id}/plan", response_class=HTMLResponse)
async def plan_task(request: Request, task_id: int, due_date: str = Form(None)):
    """Запланировать задачу на дату"""
    from datetime import date
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            try:
                if due_date:
                    day, month = due_date.split(".")
                    task.due_date = date(date.today().year, int(month), int(day))
                else:
                    task.due_date = date.today()
                await db.commit()
                return HTMLResponse(content=await get_tasks_today(db, request))
            except Exception as e:
                return HTMLResponse(content=f'<div class="text-red-400 p-4">❌ Ошибка: {e}</div>', status_code=400)
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


# ---- Список покупок (Shopping List) ----

@router.get("/shopping", response_class=HTMLResponse)
async def shopping_page(request: Request):
    """Страница списка покупок и бытовых задач"""
    async with async_session() as db:
        # 1. Элементы списка покупок (простые)
        result = await db.execute(
            select(ShoppingItem).order_by(ShoppingItem.is_purchased.asc(), ShoppingItem.created_at.desc())
        )
        items = list(result.scalars().all())

        # 2. Задачи категории "Быт" или "Покупки" из основного списка
        # Ищем категории по ключевым словам
        cat_result = await db.execute(
            select(Category).where(
                (Category.name.ilike("%быт%")) | 
                (Category.name.ilike("%покупк%")) | 
                (Category.name.ilike("%семья%"))
            )
        )
        household_categories = cat_result.scalars().all()
        household_cat_ids = [c.id for c in household_categories]

        household_tasks = []
        if household_cat_ids:
            task_result = await db.execute(
                select(Task)
                .options(selectinload(Task.category))
                .where(
                    Task.category_id.in_(household_cat_ids),
                    Task.is_archived == False
                )
                .order_by(Task.status.desc(), Task.created_at.desc())
            )
            household_tasks = task_result.scalars().all()

        total = len(items)
        purchased = sum(1 for item in items if item.is_purchased)
        remaining = total - purchased

    return templates.TemplateResponse("shopping.html", {
        "request": request,
        "items": items,
        "household_tasks": household_tasks,
        "household_cat_ids": household_cat_ids, # Теперь передаем
        "total": total,
        "purchased": purchased,
        "remaining": remaining,
    })

@router.post("/api/shopping/create", response_class=HTMLResponse)
async def create_shopping_item(
    request: Request,
    title: str = Form(...),
):
    """Создать элемент списка покупок"""
    async with async_session() as db:
        item = ShoppingItem(
            title=title,
        )
        db.add(item)
        await db.commit()
        
        # Перезагружаем весь список
        result = await db.execute(
            select(ShoppingItem).order_by(ShoppingItem.is_purchased.asc(), ShoppingItem.created_at.desc())
        )
        items = list(result.scalars().all())
        
        total = len(items)
        purchased = sum(1 for item in items if item.is_purchased)
        remaining = total - purchased

    # Возвращаем только обновленный список элементов и статистику
    from jinja2 import Template
    list_template = Template('''
        {% if items %}
            {% for item in items %}
            <div class="bg-dark-800 rounded-lg p-2 px-3 border border-dark-700 hover:border-purple-600 transition flex items-center gap-2 {{ 'opacity-40' if item.is_purchased else '' }}" 
                 id="item-{{ item.id }}">
                <!-- Чекбокс выполнения -->
                <form hx-post="/api/shopping/{{ item.id }}/toggle"
                      hx-target="#shopping-list"
                      hx-swap="innerHTML"
                      class="flex-shrink-0">
                    <button type="submit" class="text-lg {{ 'text-green-400' if item.is_purchased else 'text-gray-600 hover:text-green-400' }}">
                        {% if item.is_purchased %}✅{% else %}⬜{% endif %}
                    </button>
                </form>

                <!-- Информация о продукте -->
                <div class="flex-1 min-w-0">
                    <span class="font-medium text-sm text-white truncate block {{ 'line-through text-gray-500' if item.is_purchased else '' }}">
                        {{ item.title }}
                    </span>
                </div>

                <!-- Удаление -->
                <form hx-delete="/api/shopping/{{ item.id }}"
                      hx-target="#shopping-list"
                      hx-swap="innerHTML"
                      class="flex-shrink-0">
                    <button type="submit" class="text-gray-500 hover:text-red-400 text-xs" title="Удалить">🗑</button>
                </form>
            </div>
            {% endfor %}
        {% else %}
            <div class="col-span-full text-center py-10 text-gray-500">
                <p>Список покупок пуст 🛒</p>
            </div>
        {% endif %}
    ''')
    
    stats_html = f'''
        <script>
            document.getElementById('total-count').textContent = '{total}';
            document.getElementById('purchased-count').textContent = '{purchased}';
            document.getElementById('remaining-count').textContent = '{remaining}';
        </script>
    '''
    
    return HTMLResponse(content=list_template.render(items=items) + stats_html)


@router.post("/api/shopping/{item_id}/toggle", response_class=HTMLResponse)
async def toggle_shopping_item(request: Request, item_id: int):
    """Переключить статус покупки"""
    async with async_session() as db:
        result = await db.execute(select(ShoppingItem).where(ShoppingItem.id == item_id))
        item = result.scalar_one_or_none()
        
        if item:
            item.is_purchased = not item.is_purchased
            if item.is_purchased:
                item.purchased_at = datetime.utcnow()
            else:
                item.purchased_at = None
            await db.commit()
            
            # Перезагружаем весь список для обновления
            result = await db.execute(
                select(ShoppingItem).order_by(ShoppingItem.is_purchased.asc(), ShoppingItem.created_at.desc())
            )
            items = list(result.scalars().all())
            
            total = len(items)
            purchased = sum(1 for i in items if i.is_purchased)
            remaining = total - purchased
            
            from jinja2 import Template
            list_template = Template('''
                {% if items %}
                    {% for item in items %}
                    <div class="bg-dark-800 rounded-lg p-4 border border-dark-700 hover:border-purple-600 transition flex items-center gap-3 {{ 'opacity-50' if item.is_purchased else '' }}" 
                         id="item-{{ item.id }}">
                        <!-- Чекбокс выполнения -->
                        <form hx-post="/api/shopping/{{ item.id }}/toggle"
                              hx-target="#shopping-list"
                              hx-swap="innerHTML"
                              class="flex-shrink-0">
                            <button type="submit" class="text-xl {{ 'text-green-400' if item.is_purchased else 'text-gray-600 hover:text-green-400' }}">
                                {% if item.is_purchased %}✅{% else %}⬜{% endif %}
                            </button>
                        </form>

                        <!-- Информация о продукте -->
                        <div class="flex-1">
                            <span class="font-medium text-white {{ 'line-through text-gray-500' if item.is_purchased else '' }}">
                                {{ item.title }}
                            </span>
                        </div>

                        <!-- Удаление -->
                        <form hx-delete="/api/shopping/{{ item.id }}"
                              hx-target="#shopping-list"
                              hx-swap="innerHTML"
                              class="flex-shrink-0">
                            <button type="submit" class="text-red-400 hover:text-red-300" title="Удалить">🗑</button>
                        </form>
                    </div>
                    {% endfor %}
                {% else %}
                    <div class="text-center py-10 text-gray-500">
                        <p>Список покупок пуст 🛒</p>
                        <p class="text-sm mt-2">Введите название продукта и нажмите Enter</p>
                    </div>
                {% endif %}
            ''')
            
            stats_html = f'''
                <script>
                    document.getElementById('total-count').textContent = '{total}';
                    document.getElementById('purchased-count').textContent = '{purchased}';
                    document.getElementById('remaining-count').textContent = '{remaining}';
                </script>
            '''
            
            return HTMLResponse(content=list_template.render(items=items) + stats_html)
    
    return HTMLResponse(content='<div class="hidden"></div>')


@router.post("/api/shopping/restore-all", response_class=HTMLResponse)
async def restore_all_shopping_items(request: Request):
    """Сбросить статус 'куплено' для всех товаров"""
    async with async_session() as db:
        from sqlalchemy import update
        await db.execute(
            update(ShoppingItem).where(ShoppingItem.is_purchased == True).values(is_purchased=False, purchased_at=None)
        )
        await db.commit()
        
        # Перезагружаем список
        result = await db.execute(
            select(ShoppingItem).order_by(ShoppingItem.is_purchased.asc(), ShoppingItem.created_at.desc())
        )
        items = list(result.scalars().all())
        total = len(items)
        purchased = 0
        remaining = total

    from jinja2 import Template
    # Используем тот же компактный шаблон (копирую его из метода выше)
    list_template = Template('''
        {% if items %}
            {% for item in items %}
            <div class="bg-dark-800 rounded-lg p-2 px-3 border border-dark-700 hover:border-purple-600 transition flex items-center gap-2 {{ 'opacity-40' if item.is_purchased else '' }}" 
                 id="item-{{ item.id }}">
                <form hx-post="/api/shopping/{{ item.id }}/toggle" hx-target="#shopping-list" hx-swap="innerHTML" class="flex-shrink-0">
                    <button type="submit" class="text-lg {{ 'text-green-400' if item.is_purchased else 'text-gray-600 hover:text-green-400' }}">
                        {% if item.is_purchased %}✅{% else %}⬜{% endif %}
                    </button>
                </form>
                <div class="flex-1 min-w-0">
                    <span class="font-medium text-sm text-white truncate block {{ 'line-through text-gray-500' if item.is_purchased else '' }}">
                        {{ item.title }}
                    </span>
                </div>
                <form hx-delete="/api/shopping/{{ item.id }}" hx-target="#shopping-list" hx-swap="innerHTML" class="flex-shrink-0">
                    <button type="submit" class="text-gray-500 hover:text-red-400 text-xs" title="Удалить">🗑</button>
                </form>
            </div>
            {% endfor %}
        {% else %}
            <div class="col-span-full text-center py-10 text-gray-500">
                <p>Список покупок пуст 🛒</p>
            </div>
        {% endif %}
    ''')
    
    stats_html = f'''
        <script>
            document.getElementById('total-count').textContent = '{total}';
            document.getElementById('purchased-count').textContent = '{purchased}';
            document.getElementById('remaining-count').textContent = '{remaining}';
        </script>
    '''
    return HTMLResponse(content=list_template.render(items=items) + stats_html)


@router.post("/api/shopping/bulk-create", response_class=HTMLResponse)
async def bulk_create_shopping_items(
    request: Request,
    titles: str = Form(...),
):
    """Создать несколько элементов списка покупок из текста (по одному на строку)"""
    lines = [line.strip() for line in titles.split('\n') if line.strip()]
    
    async with async_session() as db:
        for title in lines:
            # Убираем лишние символы в начале (буллиты, дефисы)
            clean_title = title.lstrip('•-*+ ').strip()
            if clean_title:
                item = ShoppingItem(title=clean_title)
                db.add(item)
        await db.commit()
        
        # Перезагружаем список
        result = await db.execute(
            select(ShoppingItem).order_by(ShoppingItem.is_purchased.asc(), ShoppingItem.created_at.desc())
        )
        items = list(result.scalars().all())
        total = len(items)
        purchased = sum(1 for i in items if i.is_purchased)
        remaining = total - purchased

    # Тот же компактный шаблон
    from jinja2 import Template
    list_template = Template('''
        {% if items %}
            {% for item in items %}
            <div class="bg-dark-800 rounded-lg p-2 px-3 border border-dark-700 hover:border-purple-600 transition flex items-center gap-2 {{ 'opacity-40' if item.is_purchased else '' }}" 
                 id="item-{{ item.id }}">
                <form hx-post="/api/shopping/{{ item.id }}/toggle" hx-target="#shopping-list" hx-swap="innerHTML" class="flex-shrink-0">
                    <button type="submit" class="text-lg {{ 'text-green-400' if item.is_purchased else 'text-gray-600 hover:text-green-400' }}">
                        {% if item.is_purchased %}✅{% else %}⬜{% endif %}
                    </button>
                </form>
                <div class="flex-1 min-w-0">
                    <span class="font-medium text-sm text-white truncate block {{ 'line-through text-gray-500' if item.is_purchased else '' }}">
                        {{ item.title }}
                    </span>
                </div>
                <form hx-delete="/api/shopping/{{ item.id }}" hx-target="#shopping-list" hx-swap="innerHTML" class="flex-shrink-0">
                    <button type="submit" class="text-gray-500 hover:text-red-400 text-xs" title="Удалить">🗑</button>
                </form>
            </div>
            {% endfor %}
        {% else %}
            <div class="col-span-full text-center py-10 text-gray-500">
                <p>Список покупок пуст 🛒</p>
            </div>
        {% endif %}
    ''')
    
    stats_html = f'''
        <script>
            document.getElementById('total-count').textContent = '{total}';
            document.getElementById('purchased-count').textContent = '{purchased}';
            document.getElementById('remaining-count').textContent = '{remaining}';
        </script>
    '''
    return HTMLResponse(content=list_template.render(items=items) + stats_html)

