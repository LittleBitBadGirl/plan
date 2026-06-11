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
    _shopping_stats_oob,
    _shopping_list_response,
)

router = APIRouter()


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
async def archive_page(
    request: Request,
    page: int = 1,
    shop_page: int = 1,
    limit: int = 50,
):
    """Архив — задачи (item_kind=task) и покупки (item_kind=purchase) отдельно."""
    offset = (page - 1) * limit
    shop_offset = (shop_page - 1) * limit

    async with async_session() as db:
        total_result = await db.execute(
            select(func.count(Task.id)).where(
                Task.is_archived == True,
                Task.item_kind == "task",
            )
        )
        total = total_result.scalar() or 0

        result = await db.execute(
            select(Task)
            .options(selectinload(Task.category))
            .where(Task.is_archived == True, Task.item_kind == "task")
            .order_by(Task.completed_at.desc(), Task.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        tasks = list(result.scalars().all())

        shop_total_result = await db.execute(
            select(func.count(ShoppingItem.id)).where(
                ShoppingItem.is_archived == True,
                ShoppingItem.item_kind == "purchase",
            )
        )
        shop_total = shop_total_result.scalar() or 0

        shop_result = await db.execute(
            select(ShoppingItem)
            .where(ShoppingItem.is_archived == True, ShoppingItem.item_kind == "purchase")
            .order_by(ShoppingItem.purchased_at.desc(), ShoppingItem.created_at.desc())
            .offset(shop_offset)
            .limit(limit)
        )
        shopping_archived = list(shop_result.scalars().all())

        has_prev = page > 1
        has_next = offset + limit < total
        shop_has_prev = shop_page > 1
        shop_has_next = shop_offset + limit < shop_total

    return templates.TemplateResponse(request, "archive.html", {
        "request": request,
        "tasks": tasks,
        "total": total,
        "page": page,
        "limit": limit,
        "has_prev": has_prev,
        "has_next": has_next,
        "prev_page": page - 1,
        "next_page": page + 1,
        "shopping_archived": shopping_archived,
        "shop_total": shop_total,
        "shop_page": shop_page,
        "shop_has_prev": shop_has_prev,
        "shop_has_next": shop_has_next,
        "shop_prev_page": shop_page - 1,
        "shop_next_page": shop_page + 1,
    })


@router.post("/archive/shopping/{item_id}/restore", response_class=HTMLResponse)
async def restore_shopping_from_archive(item_id: int):
    """Вернуть покупку в активный список."""
    async with async_session() as db:
        result = await db.execute(select(ShoppingItem).where(ShoppingItem.id == item_id))
        item = result.scalar_one_or_none()
        if item:
            item.is_archived = False
            item.is_purchased = False
            item.purchased_at = None
            await db.commit()
            return HTMLResponse(f'<div id="archive-shop-{item_id}" class="hidden"></div>')
    return HTMLResponse('<div class="text-red-400 p-4">Ошибка восстановления</div>')
