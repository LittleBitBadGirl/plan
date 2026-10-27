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
    get_tasks_today,
    _strip_emoji,
    _render_shopping_list,
    _shopping_stats_oob,
    _shopping_list_response,
)

router = APIRouter()


@router.post("/api/categories/quick-create", response_class=HTMLResponse)
async def quick_create_category(
    request: Request,
    name: str = Form(...),
    parent_id: str = Form(""),
):
    """Быстрое создание категории задач inline — возвращает обновлённый <select>"""
    async with async_session() as db:
        final_parent_id = int(parent_id) if parent_id else None
        is_global = final_parent_id is None

        # Проверка дубля
        dup = await db.execute(
            select(Category).where(
                Category.name == name,
                Category.type == 'task',
                Category.parent_id == final_parent_id,
            )
        )
        new_cat = dup.scalar_one_or_none()
        if not new_cat:
            new_cat = Category(
                name=name,
                is_global=is_global,
                parent_id=final_parent_id,
                type='task',
            )
            db.add(new_cat)
            await db.commit()
            await db.refresh(new_cat)

        # Загружаем все task-категории для обновлённого select
        cats_result = await db.execute(
            select(Category)
            .where(Category.type == 'task')
            .order_by(Category.is_global.desc(), Category.name)
        )
        categories = cats_result.scalars().all()

    # Строим HTML для <select> options + выделяем только что созданную
    options_html = '<option value="">Без категории</option>\n'
    for cat in categories:
        if cat.is_global:
            options_html += f'<optgroup label="{cat.name}">\n'
            for sub in categories:
                if sub.parent_id == cat.id:
                    selected = 'selected' if sub.id == new_cat.id else ''
                    options_html += f'  <option value="{sub.id}" {selected}>↳ {sub.name}</option>\n'
            options_html += '</optgroup>\n'

    return HTMLResponse(content=options_html)


@router.post("/categories/create", response_class=HTMLResponse)
async def create_category_from_form(
    request: Request,
    name: str = Form(...),
    parent_id: str = Form(""),
    type: str = Form(""),
):
    """Создать категорию из формы веб-интерфейса.

    Тип приходит из формы (скрытое поле): задачи создаются во вкладке Бэклога,
    финансы — во вкладке Финансов. После создания возвращаем на ту же вкладку.
    """
    cat_type = (type or request.query_params.get("type") or "task").strip()
    if cat_type not in ("task", "finance"):
        cat_type = "task"

    async with async_session() as db:
        # Если выбран родитель, создаем подкатегорию
        final_parent_id = int(parent_id) if parent_id else None
        is_global = final_parent_id is None

        category = Category(
            name=name,
            is_global=is_global,
            parent_id=final_parent_id,
            type=cat_type,
        )
        db.add(category)
        await db.commit()

    from fastapi.responses import RedirectResponse

    target = "/finance?view=categories" if cat_type == "finance" else "/backlog?view=categories"
    return RedirectResponse(url=target, status_code=303)


@router.get("/categories")
async def categories_page():
    """Категории разделены по назначению и живут там, где нужны.

    Категории задач — вкладка в Бэклоге (рядом с самими задачами), финансовые —
    вкладка в Финансах. Старый общий адрес ведёт к задачам.
    """
    return RedirectResponse(url="/backlog?view=categories", status_code=302)


@router.get("/categories/{category_id}/edit-form", response_class=HTMLResponse)
async def get_category_edit_form(category_id: int):
    """Вернуть инлайн-форму редактирования названия категории"""
    async with async_session() as db:
        result = await db.execute(select(Category).where(Category.id == category_id))
        category = result.scalar_one_or_none()
        if not category:
            return HTMLResponse("Ошибка")
        
    display_name = _strip_emoji(category.name)
    return HTMLResponse(f"""
        <form hx-post="/categories/{category_id}/edit" hx-swap="outerHTML" class="flex gap-2 items-center">
            <input type="text" name="name" value="{display_name}"
                   class="bg-dark-900 border border-accent rounded px-2 py-1 text-sm text-white focus:outline-none w-full">
            <button type="submit" class="text-green-400 text-xs">OK</button>
            <button type="button" onclick="window.location.reload()" class="text-gray-500 text-xs">×</button>
        </form>
    """)


@router.post("/categories/{parent_id}/sub/create", response_class=HTMLResponse)
async def create_subcategory_inline(parent_id: int, name: str = Form(...)):
    """Создать подкатегорию прямо из карточки родителя.
    Тип наследуется от родителя, чтобы финансовая подкатегория не стала задачной.
    """
    async with async_session() as db:
        parent_res = await db.execute(select(Category).where(Category.id == parent_id))
        parent = parent_res.scalar_one_or_none()
        inherited_type = parent.type if parent else "task"
        new_sub = Category(name=name, parent_id=parent_id, is_global=False, type=inherited_type)
        db.add(new_sub)
        await db.commit()

    return HTMLResponse(content='<script>window.location.reload()</script>')


@router.delete("/api/categories/{category_id}/safe")
async def delete_finance_category_safe(category_id: int):
    """Удалить категорию, перенеся записи в цель по умолчанию.

    Родитель/подкатегория → транзакции и задачи к родителю; корневая → в
    «Прочее» того же типа. Оставлен для обратной совместимости: интерфейс
    теперь спрашивает цель сам и зовёт обычный DELETE с `move_to`.
    """
    from fastapi.responses import JSONResponse
    from app.services.category_delete_service import default_target, delete_category_with_move

    async with async_session() as db:
        cat_res = await db.execute(select(Category).where(Category.id == category_id))
        cat = cat_res.scalar_one_or_none()
        if not cat:
            return JSONResponse({"error": "not found"}, status_code=404)

        target = await default_target(db, cat)
        # Нет ни родителя, ни «Прочее» — уводим записи в «без категории»,
        # иначе старый /safe начал бы отдавать 409 там, где раньше работал.
        conflict = await delete_category_with_move(db, cat, target, null_target=target is None)
        if conflict:
            return JSONResponse(conflict, status_code=409)
        await db.commit()

    return JSONResponse({"ok": True})


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
            display_name = _strip_emoji(category.name)
            if category.is_global:
                return HTMLResponse(f"""
                    <h2 class="text-lg font-bold text-white" id="cat-title-{category_id}">
                        {display_name}
                    </h2>
                """)
            else:
                return HTMLResponse(f"""
                    <div class="flex items-center justify-between group/item p-2 hover:bg-dark-700/30 rounded transition-colors" id="cat-title-{category_id}">
                        <span class="text-sm text-gray-300 flex items-center gap-2">
                            <span class="text-gray-600">└</span>
                            {display_name}
                        </span>
                        <div class="flex items-center gap-2 opacity-0 group-hover/item:opacity-100 transition-opacity">
                            <button hx-get="/categories/{category_id}/edit-form" hx-target="#cat-title-{category_id}" class="text-xs text-gray-500 hover:text-white uppercase" title="Изменить">ред.</button>
                            <button type="button" onclick="deleteCategoryFlow({category_id})" class="text-red-500 hover:text-red-400 text-xs uppercase" title="Удалить">удл.</button>
                        </div>
                    </div>
                """)
    return HTMLResponse("Ошибка")
