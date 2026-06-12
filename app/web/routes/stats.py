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
    _shopping_stats_oob,
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


@router.get("/api/hermes/analyze", response_class=HTMLResponse)
async def hermes_analyze(request: Request):
    """Показать последний анализ Hermes из ai_reports или запустить прямой."""
    from datetime import date, timedelta

    async with async_session() as db:
        today = date.today()

        # Сначала проверяем — есть ли готовый анализ от Hermes в ai_reports
        report_res = await db.execute(
            select(AIReport)
            .where(AIReport.source == "hermes", AIReport.status == "done")
            .order_by(AIReport.report_date.desc())
            .limit(1)
        )
        report = report_res.scalar_one_or_none()
        if report:
            return HTMLResponse(f"""
<div class="p-5 sm:p-6 bg-dark-800">
    <p class="text-xs text-purple-400/80 mb-3">Анализ Hermes от {report.report_date.strftime('%d.%m.%Y')}</p>
    <div class="text-gray-300 leading-relaxed text-sm sm:text-base whitespace-pre-wrap">{report.content}</div>
</div>
""")

        # Если нет готового — запускаем прямой анализ (fallback)
        return await _direct_analyze(db, today)


async def _direct_analyze(db, today):
    """Прямой анализ БД (без ai_reports)."""
    from datetime import timedelta

    # ── Блок 1: Болячки ──
    pain_lines = []

    pain_res = await db.execute(
        select(Task)
        .options(selectinload(Task.category))
        .where(
            Task.status.in_(["новая", "в работе"]),
            Task.is_archived == False,
            (Task.postpones >= 2) | (Task.chronic_task == True),
        )
        .order_by(Task.postpones.desc())
        .limit(10)
    )
    pain_tasks = pain_res.scalars().all()
    if pain_tasks:
        pain_lines.append("### Хронические откладывания\n")
        for t in pain_tasks:
            size_str = f" [{t.size}]" if t.size else ""
            chronic_str = " CHRONIC" if t.chronic_task else ""
            cat = t.category.name if t.category else "?"
            due = t.due_date or t.created_at.date() if t.created_at else today
            days_open = (today - due).days if due else 0
            days_str = f"{days_open}д" if days_open > 0 else "сегодня"
            flags = []
            if t.postpones >= 3 and not t.size:
                flags.append("needs_decomposition — крупнее чем кажется. Пометь L или XL")
            elif t.postpones >= 3 and t.size in ("L", "XL"):
                flags.append(f"OK: размер {t.size} объясняет переносы")
            if t.size in ("L", "XL") and days_open > 7:
                flags.append("крупная + давно открыта — нужен план")
            pain_lines.append(f"- **{t.postpones}x** | {days_str} | {t.title[:70]}{size_str}{chronic_str} | {cat}")
            for f in flags:
                pain_lines.append(f"  - {f}")

    from app.models.recurring import RecurringTask as RT
    rec_res = await db.execute(select(RT).where(RT.is_active == True, RT.completed_count == 0))
    rec_tasks = rec_res.scalars().all()
    if rec_tasks:
        pain_lines.append("\n### Recurring без выполнений\n")
        for r in rec_tasks:
            if r.start_date:
                days_since = (today - r.start_date).days
                if days_since > 14:
                    pain_lines.append(f"- [{r.recurrence_type}] {r.title[:55]} | старт: {r.start_date} ({days_since}д)")

    lxl_res = await db.execute(
        select(Task).options(selectinload(Task.category)).where(
            Task.status.in_(["новая", "в работе"]), Task.is_archived == False,
            Task.size.in_(["L", "XL"]), Task.postpones == 0,
        ).order_by(Task.size.desc(), Task.created_at.desc()).limit(8)
    )
    lxl_tasks = lxl_res.scalars().all()
    if lxl_tasks:
        pain_lines.append("\n### Крупные задачи без откладываний\n")
        for t in lxl_tasks:
            cat = t.category.name if t.category else "?"
            pain_lines.append(f"- [{t.size}] {t.title[:60]} | {cat}")
    else:
        pain_lines.append("\n### Крупные задачи без откладываний\n")
        pain_lines.append("Пока нет помеченных L/XL задач")

    pattern_lines = ["### Завершения по дням (30 дней)\n"]
    dow_res = await db.execute(
        select(func.strftime("%w", Task.completed_at).label("dow"), func.count(Task.id).label("cnt"))
        .where(Task.status == "выполнена", Task.completed_at >= today - timedelta(days=30))
        .group_by("dow").order_by("dow")
    )
    dow_map = {"0": "Вс", "1": "Пн", "2": "Вт", "3": "Ср", "4": "Чт", "5": "Пт", "6": "Сб"}
    workday_total = 0
    for row in dow_res.all():
        d = dow_map.get(row.dow, row.dow)
        cnt = row.cnt
        bar = "█" * cnt
        note = " (выходной)" if d in ("Сб", "Вс") else ""
        if d not in ("Сб", "Вс"): workday_total += cnt
        pattern_lines.append(f"- {d}: {cnt:>2} {bar}{note}")
    pattern_lines.append(f"  Рабочие дни: {workday_total}, ~{round(workday_total/22)}/день")

    pattern_lines.append("\n### Время завершения\n")
    time_res = await db.execute(
        select(func.count(Task.id).label("cnt"), func.strftime("%H", Task.completed_at).label("hour"))
        .where(Task.status == "выполнена", Task.completed_at >= today - timedelta(days=30))
        .group_by("hour").order_by("hour")
    )
    blocks = {"Утро (до 10)": 0, "День (10-14)": 0, "После обеда (14-18)": 0, "Вечер (18+)": 0}
    for row in time_res.all():
        h = int(row.hour) if row.hour else 0
        if h < 10: blocks["Утро (до 10)"] += row.cnt
        elif h < 14: blocks["День (10-14)"] += row.cnt
        elif h < 18: blocks["После обеда (14-18)"] += row.cnt
        else: blocks["Вечер (18+)"] += row.cnt
    for label, cnt in blocks.items():
        pattern_lines.append(f"- {label}: {cnt}")

    pattern_lines.append("\n### Темп по неделям\n")
    week_res = await db.execute(
        select(func.strftime("%Y-W%W", Task.completed_at).label("week"), func.count(Task.id).label("cnt"))
        .where(Task.status == "выполнена", Task.completed_at >= today - timedelta(days=35))
        .group_by("week").order_by("week")
    )
    for row in week_res.all():
        bar = "█" * (row.cnt // 2)
        pattern_lines.append(f"- {row.week}: {row.cnt:>2} {bar}")

    pred_lines = ["### Прогноз бэклога\n"]
    avg_res = await db.execute(
        select(func.count(Task.id)).where(Task.status == "выполнена", Task.completed_at >= today - timedelta(days=14))
    )
    avg_daily = round((avg_res.scalar() or 0) / 14, 1)
    backlog_res = await db.execute(
        select(func.count(Task.id)).where(Task.status.in_(["новая", "в работе"]), Task.is_archived == False)
    )
    backlog = backlog_res.scalar() or 0
    pred_lines.append(f"- Темп: **{avg_daily}** задач/день")
    pred_lines.append(f"- Активных: **{backlog}**")
    if avg_daily > 0:
        days = round(backlog / avg_daily)
        eta = today + timedelta(days=days)
        pred_lines.append(f"- Закрытие: **{days} дней** → {eta.strftime('%d.%m.%Y')}")

    nd_res = await db.execute(
        select(Task).options(selectinload(Task.category)).where(
            Task.status.in_(["новая", "в работе"]), Task.is_archived == False,
            Task.postpones >= 3, Task.size == None,
        ).order_by(Task.postpones.desc())
    )
    nd_tasks = nd_res.scalars().all()
    pred_lines.append("\n### needs_decomposition (3+ postpones, без size)\n")
    if nd_tasks:
        for t in nd_tasks:
            cat = t.category.name if t.category else "?"
            due = t.due_date or (t.created_at.date() if t.created_at else today)
            days_open = (today - due).days if due else 0
            pred_lines.append(f"- {t.postpones}x | {days_open}д | {t.title[:60]} | {cat}")
    else:
        pred_lines.append("Нет кандидатов")

    all_lines = (
        ["## Блок 1: Болячки\n"] + pain_lines
        + ["\n## Блок 2: Паттерны\n"] + pattern_lines
        + ["\n## Блок 4: Предсказания\n"] + pred_lines
    )
    html_content = "<br>".join(all_lines).replace("\n", "<br>")
    return HTMLResponse(f"""
<div class="p-5 sm:p-6 bg-dark-800">
    <div class="text-gray-300 leading-relaxed text-sm sm:text-base">{html_content}</div>
</div>
""")


@router.post("/api/hermes/request-analysis", response_class=HTMLResponse)
async def request_hermes_analysis(request: Request):
    """Запросить анализ у Hermes — пишет pending в ai_reports, cron подхватит."""
    from datetime import date

    async with async_session() as db:
        today = date.today()

        # Проверяем: нет ли уже pending от Hermes
        existing = await db.execute(
            select(AIReport).where(
                AIReport.report_date == today,
                AIReport.source == "hermes",
                AIReport.status == "pending",
            )
        )
        if existing.scalar_one_or_none():
            return HTMLResponse("""
<div class="p-5 sm:p-6 bg-dark-800">
    <p class="text-yellow-400 text-sm">Hermes уже работает. Обнови через минуту.</p>
    <button hx-get="/api/hermes/analyze"
            hx-target="#ai-analysis-inner" hx-swap="innerHTML"
            class="mt-3 px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white text-sm font-bold rounded-xl transition-all">
        Обновить
    </button>
</div>
""")

        # Удаляем старый DeepSeek-отчёт за сегодня (unique constraint на report_date)
        old = await db.execute(
            select(AIReport).where(AIReport.report_date == today)
        )
        for r in old.scalars().all():
            await db.delete(r)

        # Создаём pending-запрос
        report = AIReport(
            report_date=today,
            content="Запрос отправлен Hermes...",
            source="hermes",
            status="pending",
        )
        db.add(report)
        await db.commit()

    return HTMLResponse("""
<div class="p-5 sm:p-6 bg-dark-800">
    <p class="text-purple-400 text-sm font-bold">Запрос отправлен Hermes.</p>
    <p class="text-gray-500 text-xs mt-1">Анализ появится здесь через минуту.</p>
    <button hx-get="/api/hermes/analyze"
            hx-target="#ai-analysis-inner" hx-swap="innerHTML"
            class="mt-3 px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white text-sm font-bold rounded-xl transition-all">
        Обновить
    </button>
</div>
""")
