"""Страница цикла для врача — вынесена с /stats отдельным адресом."""

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.db.database import async_session
from app.models.period_entry import PeriodEntry
from app.web.deps import templates, compute_period_data

router = APIRouter()


@router.get("/cycle", response_class=HTMLResponse)
async def cycle_page(request: Request):
    """Накопленные данные цикла: сводка, таблица циклов и текст для врача."""
    async with async_session() as db:
        period_res = await db.execute(select(PeriodEntry).order_by(PeriodEntry.date))
        entries = period_res.scalars().all()

    period_stats = compute_period_data(list(entries), date.today())

    return templates.TemplateResponse(request, "cycle.html", {
        "request": request,
        "period_stats": period_stats,
    })
