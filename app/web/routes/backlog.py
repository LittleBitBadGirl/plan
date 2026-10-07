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
    get_tasks_today,
    build_day_pool_context,
    render_day_pool_block,
    work_task_filter,
    not_work_task_filter,
    _strip_emoji,
    _render_shopping_list,
    _shopping_stats_oob,
    _shopping_list_response,
)

from app.services.day_pool_service import take_to_day
from app.services.task_delete_service import delete_tasks_hard
from app.services.ai_service import ai_service

router = APIRouter()


def _backlog_where(side: Optional[str] = None) -> list:
    """Условия бэклога: активное, не взято на день, корневое, не регулярное.

    side="work" — только рабочие задачи, side="personal" — только личные.
    Признак «рабочая» — из deps.work_task_filter: у Веры клиенты висят
    подкатегориями под «Работа», по имени подкатегории их не отловить.
    """
    where = [
        task_is_active(),
        Task.planned_for.is_(None),
        Task.parent_task_id == None,
        Task.source.is_distinct_from("recurring"),
        Task.item_kind == "task",
    ]
    if side == "work":
        where.append(work_task_filter())
    elif side == "personal":
        where.append(not_work_task_filter())
    return where


async def _load_backlog(
    db: AsyncSession, side: Optional[str] = None
) -> tuple[list[Task], dict[int, list[Task]]]:
    """Задачи бэклога и их подзадачи.

    Бэклог — это всё активное, что не взято на день (``planned_for IS NULL``).
    Прежнее условие «без даты» больше не подходит: даты у задач убраны из
    интерфейса, и задача со старым сроком иначе выпадала бы из бэклога совсем.
    Регулярные вхождения остаются в своём дне и в бэклог не попадают.
    """
    result = await db.execute(
        select(Task)
        .options(selectinload(Task.category).selectinload(Category.parent))
        .where(*_backlog_where(side))
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
    # Обе половины — в одном ответе: HTMX-действия бьют по #backlog-list
    # целиком, иначе после «взять на день» раскладка развалится.
    work_tasks, work_subtasks = await _load_backlog(db, "work")
    personal_tasks, personal_subtasks = await _load_backlog(db, "personal")
    return templates.get_template("partials/backlog_list.html").render({
        "request": request,
        "work_tasks": work_tasks,
        "personal_tasks": personal_tasks,
        "subtasks_map": {**work_subtasks, **personal_subtasks},
    })


async def _load_task_categories(db: AsyncSession) -> dict:
    """Категории задач и счётчики — для вкладки «Категории» внутри Бэклога.

    У каждой категории два числа: активные задачи и всего задач в ней (общий
    объём — включая выполненные и архив). Вера читает это как «сколько сейчас /
    сколько всего было».
    """
    result = await db.execute(
        select(Category)
        .where(Category.type == "task")
        .order_by(Category.is_global.desc(), Category.name)
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
        work_tasks, work_subtasks = await _load_backlog(db, "work")
        personal_tasks, personal_subtasks = await _load_backlog(db, "personal")
        subtasks_map = {**work_subtasks, **personal_subtasks}
        tasks = work_tasks + personal_tasks
        categories_ctx = await _load_task_categories(db) if view == "categories" else {}
        day_pool = await build_day_pool_context(db, request)

    context = {
        "request": request,
        "tasks": tasks,
        "work_tasks": work_tasks,
        "personal_tasks": personal_tasks,
        "subtasks_map": subtasks_map,
        "day_pool": day_pool,
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
            cat_stmt = select(Category).where(Category.type == "task").order_by(
                Category.is_global.desc(), Category.name
            )
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
            await delete_tasks_hard(db, task)
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

        # 4. Удаляем старую задачу из бэклога (вместе с подзадачами: иначе
        # они остались бы без родителя и висели в списке сиротами)
        await delete_tasks_hard(db, task)
        await db.commit()

        # 4. Возвращаем пустой блок (HTMX удалит элемент из списка)
        return HTMLResponse(f'<div id="task-{task_id}" class="hidden"></div>')


@router.post("/backlog/{task_id}/plan-today", response_class=HTMLResponse)
async def plan_task_today(request: Request, task_id: int):
    """HTMX: взять задачу из бэклога на сегодня (кнопка-молния).

    Ставит «взято на день» и не сбрасывает счётчик переносов — иначе терялась
    бы статистика «брал и не доделал», о которой Вера отдельно просила.
    Отдаёт список бэклога целиком плюс OOB-сводку дня: числа на экране должны
    меняться сразу после каждого взятого дела.
    """
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            return HTMLResponse(f'<div id="task-{task_id}" class="text-red-400">Ошибка</div>')

        await take_to_day(db, task)
        await db.commit()

        html = await _render_backlog_list(request, db)
        pool = await build_day_pool_context(db, request)
        return HTMLResponse(content=html + render_day_pool_block(pool, oob=True))


@router.post("/backlog/{task_id}/complete", response_class=HTMLResponse)
async def complete_from_backlog(request: Request, task_id: int):
    """HTMX: закрыть задачу из бэклога (меню «Отметить выполненной»).

    Галочка отправляет сделанное в архив, поэтому список перерисовывается
    целиком: строка должна исчезнуть, а не превратиться в надпись.
    """
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=404, detail="Задача не найдена")

        now = datetime.utcnow()
        task.status = "выполнена"
        task.completed_at = now
        task.overdue_since = None
        if task.parent_task_id is None:
            task.is_archived = True
            task.item_kind = "task"
            children = await db.execute(
                select(Task).where(
                    Task.parent_task_id == task.id,
                    Task.status != "выполнена",
                )
            )
            for child in children.scalars().all():
                child.status = "выполнена"
                child.completed_at = now
                child.is_archived = False
                child.overdue_since = None
        await db.commit()

        html = await _render_backlog_list(request, db)
        pool = await build_day_pool_context(db, request)
        return HTMLResponse(content=html + render_day_pool_block(pool, oob=True))


@router.post("/backlog/{task_id}/delete-task", response_class=HTMLResponse)
async def delete_from_backlog(request: Request, task_id: int):
    """HTMX: удалить задачу насовсем (меню «Удалить»).

    Это не архив: архив — место для сделанного (галочка), а корзина убирает
    задачу из базы вместе с её подзадачами. Ошибиться нельзя, поэтому в меню
    стоит подтверждение.
    """
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=404, detail="Задача не найдена")

        # Удаление вместе с подзадачами и со ссылками из career_impacts /
        # screenshots: sqlite здесь без PRAGMA foreign_keys и сам их не уберёт.
        await delete_tasks_hard(db, task)
        await db.commit()

        html = await _render_backlog_list(request, db)
        pool = await build_day_pool_context(db, request)
        return HTMLResponse(content=html + render_day_pool_block(pool, oob=True))


@router.post("/backlog/{task_id}/take-subtask", response_class=HTMLResponse)
async def take_subtask_to_day(request: Request, task_id: int):
    """HTMX: взять на день одну подзадачу большой задачи.

    Ответ — только сама строка (в состоянии «в дне»), чтобы раскрытый список
    подзадач не схлопывался между кликами: Вера берёт куски по одному.
    """
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        sub = result.scalar_one_or_none()
        if not sub or not sub.parent_task_id:
            raise HTTPException(status_code=404, detail="Подзадача не найдена")

        await take_to_day(db, sub)
        await db.commit()

        row = templates.get_template("partials/subtask_row.html").render({
            "request": request,
            "sub": sub,
            "parent_id": sub.parent_task_id,
            "today": date.today(),
            "subtask_context": "backlog",
        })
        pool = await build_day_pool_context(db, request)
        return HTMLResponse(content=row + render_day_pool_block(pool, oob=True))
