"""Аналитика: сводка задач и поток (прилетело против закрытого).

Блок «Анализ Гермеса» и всё, что его обслуживало, убрано 07.10.2026: Вера им не
пользовалась, а карточка занимала верх страницы и показывала старый сохранённый
отчёт. Вместе с блоком ушли запросы в ai_reports и приём результата от крона;
таблица ai_reports и её данные оставлены как есть, схему не ломаем.

Карьерный капитал и цикл живут своими страницами (/career, /cycle), здесь от них
только ссылки.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func

from app.db.database import async_session
from app.models.task import Task, task_is_active
from app.models.category import Category

from app.web.deps import (
    templates,
    get_flow_data,
    get_productivity_insights,
)

router = APIRouter()


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request, period: str = "month"):
    """Статистика"""
    async with async_session() as db:
        # Общие показатели (всегда за все время)
        completed_result = await db.execute(select(func.count(Task.id)).where(Task.status == "выполнена"))
        total_completed = completed_result.scalar() or 0

        active_result = await db.execute(select(func.count(Task.id)).where(task_is_active(), Task.status != "выполнена"))
        total_active = active_result.scalar() or 0

        cat_stats_query = (
            select(Category.name, func.count(Task.id))
            .join(Task, Task.category_id == Category.id)
            .where(Task.status == "выполнена")
            .group_by(Category.name).order_by(func.count(Task.id).desc()).limit(3)
        )
        cat_stats_result = await db.execute(cat_stats_query)
        category_distribution = cat_stats_result.all()

        insights = await get_productivity_insights(db)
        # Поток задач: сколько прилетает против того, сколько закрывается.
        flow = await get_flow_data(db, "month")

        # Карьерный капитал и цикл вынесены своими страницами (/career, /cycle):
        # на аналитике от них остались ссылки, чтобы страница не превращалась
        # в свалку и чтобы у каждого числа был один хозяин.

        # Аналитика переносов по категориям
        postpones_query = await db.execute(
            select(Category.name, 
                   func.count(Task.id).label("cnt"),
                   func.sum(Task.postpones).label("total_postpones"),
                   func.avg(Task.postpones).label("avg_postpones"))
            .join(Task, Task.category_id == Category.id)
            .where(Task.postpones > 0, Task.status != "выполнена", task_is_active())
            .group_by(Category.name)
            .order_by(func.sum(Task.postpones).desc())
        )
        postpones_stats = postpones_query.all()

    return templates.TemplateResponse(request, "stats.html", {
        "request": request,
        "total_completed": total_completed,
        "total_active": total_active,
        "category_distribution": category_distribution,
        "insights": insights,
        "flow": flow,
        "postpones_stats": postpones_stats,
    })


@router.get("/api/stats/flow", response_class=HTMLResponse)
async def get_stats_flow(request: Request, period: str = "month"):
    """Блок «Поток задач» целиком — период переключается кнопками в шапке блока."""
    if period not in ("week", "month", "year"):
        period = "month"
    async with async_session() as db:
        flow = await get_flow_data(db, period)

    return templates.TemplateResponse(request, "partials/stats_flow.html", {
        "request": request,
        "flow": flow,
    })
