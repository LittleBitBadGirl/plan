from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from urllib.parse import quote
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

    flash = request.query_params.get("flash")
    flash_title = request.query_params.get("title", "")

    return templates.TemplateResponse(request, "recurring.html", {
        "request": request,
        "recurring_tasks": recurring_tasks,
        "categories": categories,
        "today": date.today().isoformat(),
        "flash": flash,
        "flash_title": flash_title,
        "show_create_form": flash in ("duplicate", "empty_title"),
    })


@router.post("/recurring/create")
@router.post("/api/recurring/web-create")
async def create_recurring_web(
    request: Request,
    title: str = Form(...),
    category_id: str = Form(None),
    recurrence_type: str = Form(...),
    days: List[str] = Form(None),
    start_date: str = Form(None),
):
    """Создать периодическую задачу из веб-интерфейса (POST → redirect, без повторной отправки формы)."""
    title_clean = (title or "").strip()
    if not title_clean:
        return RedirectResponse(url="/recurring?flash=empty_title", status_code=303)

    category_id_int = int(category_id) if category_id and category_id.isdigit() else None

    async with async_session() as db:
        dup_filters = [
            RecurringTask.title == title_clean,
            RecurringTask.recurrence_type == recurrence_type,
            RecurringTask.is_active == True,
        ]
        if category_id_int is None:
            dup_filters.append(RecurringTask.category_id.is_(None))
        else:
            dup_filters.append(RecurringTask.category_id == category_id_int)

        existing = await db.execute(select(RecurringTask).where(*dup_filters))
        if existing.scalar_one_or_none():
            return RedirectResponse(
                url=f"/recurring?flash=duplicate&title={quote(title_clean)}",
                status_code=303,
            )

        new_rt = RecurringTask(
            title=title_clean,
            category_id=category_id_int,
            recurrence_type=recurrence_type,
            recurrence_days=days if recurrence_type == "weekly" and days else None,
            start_date=date.fromisoformat(start_date) if start_date else date.today(),
            is_active=True,
        )
        db.add(new_rt)
        await db.commit()

    return RedirectResponse(url="/recurring?flash=created", status_code=303)
