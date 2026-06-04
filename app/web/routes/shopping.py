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

from app.services.shopping_service import load_active_shopping, archive_purchased_item

@router.get("/shopping", response_class=HTMLResponse)
async def shopping_page(request: Request):
    """Страница списка покупок."""
    from app.services.shopping_service import load_active_shopping

    async with async_session() as db:
        items = await load_active_shopping(db)
        archived_count_result = await db.execute(
            select(func.count(ShoppingItem.id)).where(ShoppingItem.is_archived == True)
        )
        archived_count = archived_count_result.scalar() or 0

    return templates.TemplateResponse(request, "shopping.html", {
        "request": request,
        "items": items,
        "total": len(items),
        "remaining": len(items),
        "archived_count": archived_count,
    })


@router.post("/api/shopping/create", response_class=HTMLResponse)
async def create_shopping_item(request: Request, title: str = Form(...)):
    """Создать элемент списка покупок."""
    async with async_session() as db:
        db.add(ShoppingItem(title=title, item_kind="purchase"))
        await db.commit()
        return await _shopping_list_response(request, db)


@router.post("/api/shopping/{item_id}/toggle", response_class=HTMLResponse)
async def toggle_shopping_item(request: Request, item_id: int):
    """Отметить купленным → убрать из списка, положить в архив."""
    from app.services.shopping_service import archive_purchased_item

    async with async_session() as db:
        result = await db.execute(
            select(ShoppingItem).where(
                ShoppingItem.id == item_id,
                ShoppingItem.is_archived == False,
            )
        )
        item = result.scalar_one_or_none()
        if item:
            archive_purchased_item(item)
            await db.commit()
            return await _shopping_list_response(request, db)
    return HTMLResponse(content='<div class="hidden"></div>')


@router.delete("/api/shopping/{item_id}", response_class=HTMLResponse)
async def delete_shopping_item(request: Request, item_id: int):
    """Удалить активную позицию из списка."""
    async with async_session() as db:
        result = await db.execute(
            select(ShoppingItem).where(
                ShoppingItem.id == item_id,
                ShoppingItem.is_archived == False,
            )
        )
        item = result.scalar_one_or_none()
        if item:
            await db.delete(item)
            await db.commit()
            return await _shopping_list_response(request, db)
    raise HTTPException(status_code=404, detail="Позиция не найдена")


@router.delete("/api/shopping/clear-purchased", response_class=HTMLResponse)
async def clear_archived_shopping(request: Request):
    """Удалить все архивные покупки навсегда."""
    async with async_session() as db:
        await db.execute(
            delete(ShoppingItem).where(ShoppingItem.is_archived == True)
        )
        await db.commit()
        return await _shopping_list_response(request, db)


@router.post("/api/shopping/bulk-create", response_class=HTMLResponse)
async def bulk_create_shopping_items(request: Request, titles: str = Form(...)):
    """Создать несколько элементов списка покупок из текста."""
    lines = [line.strip() for line in titles.split("\n") if line.strip()]

    async with async_session() as db:
        for title in lines:
            clean_title = title.lstrip("•-*+ ").strip()
            if clean_title:
                db.add(ShoppingItem(title=clean_title, item_kind="purchase"))
        await db.commit()
        return await _shopping_list_response(request, db)
