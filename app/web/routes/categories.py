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
            type=request.query_params.get("type", "task")
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

        counts_result = await db.execute(
            select(Task.category_id, func.count(Task.id))
            .where(Task.is_archived == False)
            .group_by(Task.category_id)
        )
        task_counts = {row[0]: row[1] for row in counts_result.all()}

        # Разделяем категории по ТИПУ
    task_cats = [c for c in categories if c.type == 'task']
    finance_cats = [c for c in categories if c.type == 'finance']
    
    # Иерархия задач
    global_cats = [c for c in task_cats if c.is_global]
    sub_cats = {gc.id: [c for c in task_cats if c.parent_id == gc.id] for gc in global_cats}

    # Иерархия финансов (Группы -> Категории)
    fin_global_cats = [c for c in finance_cats if c.is_global]
    fin_sub_cats = {gc.id: [c for c in finance_cats if c.parent_id == gc.id] for gc in fin_global_cats}

    final_counts = task_counts.copy()
    for cat in categories:
        if not cat.is_global and cat.parent_id:
            count = task_counts.get(cat.id, 0)
            if count > 0:
                final_counts[cat.parent_id] = final_counts.get(cat.parent_id, 0) + count

    return templates.TemplateResponse(request, "categories.html", {
        "request": request,
        "global_categories": global_cats,
        "sub_categories": sub_cats,
        "fin_global_categories": fin_global_cats,
        "fin_sub_categories": fin_sub_cats,
        "categories": categories,
        "task_counts": final_counts,
        "raw_counts": task_counts,
    })


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


@router.delete("/api/categories/{category_id}", response_class=HTMLResponse)
async def delete_category_htmx(category_id: int):
    """Удалить категорию (HTMX) — для категорий задач"""
    async with async_session() as db:
        result = await db.execute(select(Category).where(Category.id == category_id))
        cat = result.scalar_one_or_none()
        if cat:
            await db.delete(cat)
            await db.commit()
            return HTMLResponse(content="")
    return HTMLResponse(status_code=404)


@router.delete("/api/categories/{category_id}/safe")
async def delete_finance_category_safe(category_id: int):
    """Удалить финансовую категорию с переносом транзакций.

    Подкатегория → транзакции переходят к родителю.
    Родительская → транзакции переходят в 'Прочее' (или null).
    """
    from fastapi.responses import JSONResponse
    from sqlalchemy import update as sa_update
    from app.models.finance import Transaction as Tx

    async with async_session() as db:
        cat_res = await db.execute(select(Category).where(Category.id == category_id))
        cat = cat_res.scalar_one_or_none()
        if not cat:
            return JSONResponse({"error": "not found"}, status_code=404)

        if cat.parent_id:
            # Подкатегория → транзакции к родителю
            fallback_id = cat.parent_id
        else:
            # Корневая → ищем "Прочее" того же типа
            prochee_res = await db.execute(
                select(Category).where(
                    Category.name == "Прочее",
                    Category.type == cat.type,
                    Category.is_global == True,
                    Category.id != category_id,
                )
            )
            prochee = prochee_res.scalar_one_or_none()
            fallback_id = prochee.id if prochee else None

            # Все дочерние подкатегории → тоже к fallback
            await db.execute(
                sa_update(Category)
                .where(Category.parent_id == category_id)
                .values(parent_id=fallback_id)
            )

        # Переносим транзакции
        await db.execute(
            sa_update(Tx)
            .where(Tx.category_id == category_id)
            .values(category_id=fallback_id)
        )

        await db.delete(cat)
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
                            <button hx-delete="/api/categories/{category_id}" hx-target="#cat-title-{category_id}" hx-swap="delete" hx-confirm="Удалить подкатегорию?" class="text-red-500 hover:text-red-400 text-xs uppercase" title="Удалить">удл.</button>
                        </div>
                    </div>
                """)
    return HTMLResponse("Ошибка")
