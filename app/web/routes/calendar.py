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

from app.services.calendar_ignore_service import decline_calendar_event
from app.services.calendar_sync_service import get_visible_events_grouped

@router.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request):
    """Календарь"""
    return templates.TemplateResponse(request, "calendar.html", {
        "request": request,
        "today": date.today().isoformat(),
    })


@router.post("/api/calendar/{event_id}/decline", response_class=HTMLResponse)
async def decline_calendar_meeting(request: Request, event_id: int):
    """«Не пойду» / «Скрыть» — скрыть встречу или всю повторяющуюся серию."""
    from app.services.calendar_ignore_service import decline_calendar_event
    from app.services.calendar_sync_service import get_visible_events_grouped

    today = date.today()
    async with async_session() as db:
        declined = await decline_calendar_event(db, event_id)
        if not declined:
            return HTMLResponse(
                content='<div id="calendar-column-blocks"></div>',
                status_code=404,
            )
        work_events, personal_events = await get_visible_events_grouped(db, today)

    return templates.TemplateResponse(
        request,
        "partials/calendar_column_blocks.html",
        {
            "request": request,
            "calendar_events": work_events,
            "calendar_personal_events": personal_events,
        },
    )
