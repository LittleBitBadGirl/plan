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
    get_productivity_insights,
    get_tasks_today,
    _strip_emoji,
    _render_shopping_list,
    _shopping_stats_script,
    _shopping_list_response,
)

router = APIRouter()

import httpx
from app.services.ai_service import ai_service


def _ai_analysis_inner_html(content: str, note: str = "") -> str:
    """Фрагмент для #ai-analysis-inner (HTMX innerHTML)."""
    note_block = (
        f'<p class="text-xs text-green-400/90 mb-3">{note}</p>' if note else ""
    )
    return f"""
<div class="p-6 sm:p-8">
    {note_block}
    <div class="text-gray-300 leading-relaxed whitespace-pre-wrap text-sm sm:text-base">{content}</div>
</div>
<div class="p-4 bg-dark-700/30 border-t border-dark-600">
    <p class="text-[10px] text-gray-500 uppercase tracking-wide mb-2">Обратная связь</p>
    <div class="flex gap-2">
        <input type="text" name="feedback"
               placeholder="Что поправить в анализе?"
               class="flex-1 bg-dark-900 border border-dark-600 rounded-lg px-3 py-2 text-sm text-white"
               hx-get="/api/ai/feedback"
               hx-trigger="keydown[key=='Enter']"
               hx-include="this"
               hx-target="#ai-analysis-inner"
               hx-swap="innerHTML">
        <button type="button" class="px-4 py-2 bg-accent text-white rounded-lg text-sm font-bold"
                hx-get="/api/ai/feedback"
                hx-include="[name='feedback']"
                hx-target="#ai-analysis-inner"
                hx-swap="innerHTML">→</button>
    </div>
</div>
"""

@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request, period: str = "month"):
    """Статистика"""
    async with async_session() as db:
        # Общие показатели (всегда за все время)
        completed_result = await db.execute(select(func.count(Task.id)).where(Task.status == "выполнена"))
        total_completed = completed_result.scalar() or 0

        active_result = await db.execute(select(func.count(Task.id)).where(Task.is_archived == False, Task.status != "выполнена"))
        total_active = active_result.scalar() or 0

        cat_stats_query = (
            select(Category.name, func.count(Task.id))
            .join(Task, Task.category_id == Category.id)
            .where(Task.status == "выполнена")
            .group_by(Category.name).order_by(func.count(Task.id).desc()).limit(3)
        )
        cat_stats_result = await db.execute(cat_stats_query)
        category_distribution = cat_stats_result.all()

        history_data = await get_history_data(db, period)
        insights = await get_productivity_insights(db)
        
        report_result = await db.execute(select(AIReport).order_by(AIReport.report_date.desc()))
        last_report = report_result.scalars().first()

        # Карьерный капитал (Impacts)
        from app.models.impact import CareerImpact
        impact_res = await db.execute(
            select(CareerImpact).order_by(CareerImpact.period_month.desc(), CareerImpact.created_at.desc())
        )
        impacts = impact_res.scalars().all()
        
        # Расчет Impact Score (соотношение побед к рутине)
        # Берем данные за последние 30 дней для актуальности
        last_30_days = date.today() - timedelta(days=30)
        impact_count = len([i for i in impacts if i.created_at.date() >= last_30_days])
        
        comp_30_res = await db.execute(
            select(func.count(Task.id)).where(Task.status == "выполнена", Task.completed_at >= last_30_days)
        )
        total_30_completed = comp_30_res.scalar() or 0
        
        impact_score = round((impact_count / total_30_completed * 100)) if total_30_completed > 0 else 0

        # Period stats
        from app.models.period_entry import PeriodEntry as PE
        period_res = await db.execute(select(PE).order_by(PE.date))
        period_entries_for_stats = period_res.scalars().all()
        period_stats = compute_period_data(list(period_entries_for_stats), date.today())

    return templates.TemplateResponse(request, "stats.html", {
        "request": request,
        "total_completed": total_completed,
        "total_active": total_active,
        "category_distribution": category_distribution,
        "insights": insights,
        "weekly_history": history_data["history"],
        "max_hist": history_data["max_val"],
        "period": period,
        "last_report": last_report,
        "career_impacts": impacts,
        "impact_score": impact_score,
        "period_stats": period_stats,
    })

@router.get("/api/stats/chart", response_class=HTMLResponse)
async def get_stats_chart(request: Request, period: str = "month"):
    """Обновление только блока графика через HTMX"""
    async with async_session() as db:
        history_data = await get_history_data(db, period)
        
    return templates.TemplateResponse(request, "partials/stats_chart.html", {
        "request": request,
        "weekly_history": history_data["history"],
        "max_hist": history_data["max_val"],
        "period": period,
    })


@router.get("/api/ai/prepare-analysis", response_class=HTMLResponse)
async def run_ai_analysis(request: Request):
    """Анализ задач через DeepSeek — с даты последнего анализа"""
    
    async with async_session() as db:
        # Находим дату последнего анализа
        last_report_res = await db.execute(
            select(func.max(AIReport.report_date))
        )
        last_date = last_report_res.scalar()
        start_date = last_date + timedelta(days=1) if last_date else date.today() - timedelta(days=7)
        today = date.today()
        
        if start_date > today:
            start_date = today
        
        # Задачи за период
        result = await db.execute(
            select(Task)
            .options(selectinload(Task.category))
            .where(
                (func.date(Task.completed_at) >= start_date) | 
                (Task.due_date >= start_date)
            )
        )
        tasks = result.scalars().all()

        if not tasks:
            return HTMLResponse(f"""
<p class="px-6 py-8 text-center text-sm text-yellow-500/90">
    Нет новых задач с {start_date.strftime('%d.%m')}.
</p>
""")

        tasks_list = []
        for t in tasks:
            status = "✅" if t.status == "выполнена" else "⏳"
            cat = t.category.name if t.category else "Без категории"
            tasks_list.append(f"- [{status}] [{cat}] {t.title}")

        tasks_context = "\n".join(tasks_list)
        
        prompt = f"""Ты — Senior Project Manager и коуч по продуктивности. 
Проанализируй итоги пользователя за период {start_date.strftime('%d.%m')} — {today.strftime('%d.%m')}.

ЗАДАЧИ:
{tasks_context}

Формат ответа (Markdown):
### Анализ периода
(2-3 предложения — общая картина)

### Положительные результаты
(конкретные успехи, проекты)

### Области для улучшения
(где просадка, что оптимизировать)

### Рекомендация
(одна конкретная)
"""

        # DeepSeek
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                api_key = settings.deepseek_api_key or settings.groq_api_key
                api_url = "https://api.deepseek.com/chat/completions" if settings.deepseek_api_key else "https://api.groq.com/openai/v1/chat/completions"
                model = "deepseek-chat" if settings.deepseek_api_key else "llama-3.3-70b-versatile"
                
                response = await client.post(
                    api_url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": "Ты — аналитик продуктивности. Пиши на русском, профессионально, без воды."},
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.3
                    },
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    analysis_content = response.json()['choices'][0]['message']['content']
                    
                    # Сохраняем
                    report = AIReport(report_date=today, content=analysis_content)
                    db.add(report)
                    await db.commit()
                    
                    return HTMLResponse(_ai_analysis_inner_html(analysis_content))
                else:
                    return HTMLResponse(
                        f'<p class="px-6 py-8 text-center text-sm text-red-400/90">Ошибка API: {response.status_code}</p>'
                    )
        except Exception as e:
            return HTMLResponse(
                f'<p class="px-6 py-8 text-center text-sm text-red-400/90">Ошибка: {str(e)}</p>'
            )


@router.get("/api/ai/feedback", response_class=HTMLResponse)
async def ai_feedback(request: Request, feedback: str = ""):
    """Повторный анализ с учётом обратной связи"""
    if not feedback.strip():
        return HTMLResponse(
            '<p class="px-6 py-6 text-center text-sm text-yellow-400/90">Введи текст обратной связи</p>'
        )
    
    async with async_session() as db:
        last = await db.execute(
            select(AIReport).order_by(AIReport.report_date.desc()).limit(1)
        )
        report = last.scalar_one_or_none()
        if not report:
            return HTMLResponse(
                '<p class="px-6 py-6 text-center text-sm text-yellow-400/90">Нет сохранённого анализа</p>'
            )
        
        tasks_res = await db.execute(
            select(Task).options(selectinload(Task.category))
            .where(Task.completed_at != None)
            .order_by(Task.completed_at.desc()).limit(50)
        )
        tasks = tasks_res.scalars().all()
        tasks_text = "\n".join([f"- {t.title} [{t.category.name if t.category else '?'}]" for t in tasks])
        
        prompt = f"""Перепиши анализ продуктивности с учётом обратной связи пользователя.

ОБРАТНАЯ СВЯЗЬ: {feedback}

ПРЕДЫДУЩИЙ АНАЛИЗ:
{report.content}

ЗАДАЧИ (контекст):
{tasks_text}

Сделай новый анализ, исправляя то, что указано в обратной связи. Тот же формат (Markdown).
"""
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                api_key = settings.deepseek_api_key or settings.groq_api_key
                api_url = "https://api.deepseek.com/chat/completions" if settings.deepseek_api_key else "https://api.groq.com/openai/v1/chat/completions"
                model = "deepseek-chat" if settings.deepseek_api_key else "llama-3.3-70b-versatile"
                
                resp = await client.post(api_url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": model, "messages": [
                        {"role": "system", "content": "Ты — аналитик. Учитывай обратную связь. Пиши на русском."},
                        {"role": "user", "content": prompt}
                    ], "temperature": 0.3},
                    timeout=30.0)
                
                if resp.status_code == 200:
                    new_content = resp.json()['choices'][0]['message']['content']
                    report.content = new_content
                    await db.commit()
                    
                    note = f"Обновлено: «{feedback[:80]}»"
                    return HTMLResponse(_ai_analysis_inner_html(new_content, note=note))
        except Exception as e:
            return HTMLResponse(
                f'<p class="px-6 py-8 text-center text-sm text-red-400/90">Ошибка: {str(e)}</p>'
            )

    return HTMLResponse(
        '<p class="px-6 py-8 text-center text-sm text-red-400/90">Неизвестная ошибка</p>'
    )
