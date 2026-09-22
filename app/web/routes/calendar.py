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

from app.services.calendar_ignore_service import decline_calendar_event
from app.services.calendar_sync_service import (
    calendar_sync_active,
    get_visible_events_grouped,
    refresh_calendar_events,
)


async def _calendar_blocks_response(
    request: Request, *, sync: bool = False, kind: str | None = None
):
    if sync:
        await refresh_calendar_events(kind=kind)

    today = date.today()
    async with async_session() as db:
        work_events, personal_events = await get_visible_events_grouped(db, today)

    return templates.TemplateResponse(
        request,
        "partials/calendar_column_blocks.html",
        {
            "request": request,
            "calendar_events": work_events,
            "calendar_personal_events": personal_events,
            "calendar_sync_active": calendar_sync_active(),
        },
    )

@router.get("/calendar")
async def calendar_page():
    """Календарь переехал во вкладку «Календарь» на странице Бэклога.

    Старая страница не удаляется из навигации одним щелчком: ссылки, закладки и
    PWA-ярлык продолжают работать — просто ведут на нужную вкладку.
    """
    return RedirectResponse(url="/backlog?view=calendar", status_code=302)


@router.post("/api/calendar/sync", response_class=HTMLResponse)
async def sync_calendar_blocks(request: Request, kind: str | None = None):
    """HTMX: подтянуть CalDAV/Google и обновить блоки встреч/личного.

    kind=work — только рабочий календарь, kind=personal — только личный:
    у каждого блока своя кнопка и она дёргает только свой календарь.
    Без kind (старые вызовы, бот, cron) тянем оба.
    """
    if kind not in (None, "work", "personal"):
        raise HTTPException(status_code=400, detail="Неизвестный календарь")
    return await _calendar_blocks_response(request, sync=True, kind=kind)


@router.post("/api/calendar/{event_id}/decline", response_class=HTMLResponse)
async def decline_calendar_meeting(request: Request, event_id: int):
    """«Не пойду» / «Скрыть» — скрыть встречу или всю повторяющуюся серию."""
    today = date.today()
    async with async_session() as db:
        declined = await decline_calendar_event(db, event_id)
        if not declined:
            return HTMLResponse(
                content='<div id="calendar-column-blocks"></div>',
                status_code=404,
            )

    return await _calendar_blocks_response(request)
