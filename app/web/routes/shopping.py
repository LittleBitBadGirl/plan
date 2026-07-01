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
    _shopping_toggle_response,
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
    async with async_session() as db:
        result = await db.execute(
            select(ShoppingItem).where(
                ShoppingItem.id == item_id,
                ShoppingItem.is_archived == False,
            )
        )
        item = result.scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Позиция не найдена")

        archive_purchased_item(item)
        await db.commit()
        return await _shopping_toggle_response(db)


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


@router.post("/api/shopping/send-to-telegram", response_class=HTMLResponse)
async def send_shopping_to_telegram():
    """Отправить активный список покупок в Telegram через бота планера."""
    import httpx
    from app.utils.logger import app_logger

    token = settings.telegram_bot_token
    chat_id = settings.telegram_admin_chat_id
    if not token or not chat_id:
        return HTMLResponse('<span class="text-red-400 text-[9px] font-bold">ТГ не настроен</span>')

    async with async_session() as db:
        items = await load_active_shopping(db)

    if not items:
        return HTMLResponse('<span class="text-gray-400 text-[9px] font-bold">Список пуст</span>')

    text = "\n".join(["🛒 Список покупок:"] + [f"• {it.title}" for it in items])

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=10.0,
            )
        if resp.status_code == 200:
            return HTMLResponse('<span class="text-green-400 text-[9px] font-bold">✓ Отправлено</span>')
        app_logger.error(f"Shopping→TG: {resp.status_code} {resp.text[:200]}")
        return HTMLResponse(f'<span class="text-red-400 text-[9px] font-bold">Ошибка {resp.status_code}</span>')
    except Exception as e:
        app_logger.error(f"Shopping→TG error: {e}")
        return HTMLResponse('<span class="text-red-400 text-[9px] font-bold">Ошибка отправки</span>')
