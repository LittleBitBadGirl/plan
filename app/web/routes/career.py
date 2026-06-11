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

from app.services.ai_service import ai_service

@router.get("/api/ai/generate-milestones", response_class=HTMLResponse)
async def generate_milestones(request: Request):
    """Генератор Карьерного капитала: выцепляем достижения из всех задач месяца"""
    today = date.today()
    first_day = today.replace(day=1)
    period_str = today.strftime('%Y-%m')

    async with async_session() as db:
        # Берем ВСЕ выполненные задачи за этот месяц
        result = await db.execute(
            select(Task)
            .options(selectinload(Task.category))
            .where(
                Task.status == "выполнена",
                Task.completed_at >= first_day,
                Task.is_archived == True
            )
        )
        tasks = result.scalars().all()

        if not tasks:
            return HTMLResponse('<div class="p-4 bg-yellow-900/20 text-yellow-500 rounded-lg">За этот месяц пока нет выполненных задач.</div>')

        # Готовим данные для Грока
        tasks_data = [
            {"title": t.title, "category": t.category.name if t.category else "Без категории"}
            for t in tasks
        ]

        # Зовем Грока для фильтрации и переписывания
        impacts = await ai_service.generate_impact_report(tasks_data)
        
        if not impacts:
            return HTMLResponse('<div class="p-4 bg-red-900/20 text-red-400 rounded-lg">Грок не нашел значимых достижений в списке или произошла ошибка.</div>')

        # Сохраняем результаты в базу
        from app.models.impact import CareerImpact
        saved_count = 0
        for item in impacts:
            impact_obj = CareerImpact(
                original_title=item.get("original_title"),
                impact_description=item.get("impact"),
                category_name=item.get("category"),
                period_month=period_str
            )
            db.add(impact_obj)
            saved_count += 1
        
        await db.commit()

        return HTMLResponse(f"""
            <div class="bg-blue-900/20 border border-blue-500/50 p-6 rounded-xl text-center">
                <p class="text-blue-400 font-bold mb-2 text-lg">Карьерный капитал пополнен</p>
                <p class="text-gray-400 text-sm mb-4">Найдено и обработано достижений: {saved_count}.</p>
                <button onclick="window.location.reload()" class="px-4 py-2 bg-accent text-white rounded-lg font-bold shadow-lg">Обновить и посмотреть</button>
            </div>
        """)

@router.get("/api/career/export", response_class=HTMLResponse)
async def export_career_capital(request: Request):
    """Экспорт всех достижений в Markdown"""
    async with async_session() as db:
        from app.models.impact import CareerImpact
        result = await db.execute(
            select(CareerImpact).order_by(CareerImpact.period_month.desc(), CareerImpact.created_at.desc())
        )
        impacts = result.scalars().all()

        if not impacts:
            return HTMLResponse("Нет данных для экспорта.")

        md_content = "# Мой Карьерный Капитал\n\n"
        current_month = ""
        
        for imp in impacts:
            if imp.period_month != current_month:
                current_month = imp.period_month
                md_content += f"\n## Период: {current_month}\n"
            
            md_content += f"### {imp.category_name}\n"
            md_content += f"**Что сделано:** {imp.original_title}\n"
            md_content += f"**Impact:** {imp.impact_description}\n"
            md_content += "---\n"

        from fastapi.responses import Response
        return Response(
            content=md_content,
            media_type="text/markdown",
            headers={"Content-Disposition": f"attachment; filename=career_capital_{date.today().isoformat()}.md"}
        )
