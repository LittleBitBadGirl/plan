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

# Категории, которые считаются карьерно-значимыми
CAREER_CATEGORIES = [
    "Пет-проекты",
    "СБТ",
    "ЗМ",
    "Тендеры",
    "Документы",
    "Работа",
    "АИЖ",
    "СМ Б24",
    "команда",
    "AI / ИИ",
    "Блог",
    "Выступления / Конференции",
    "Карьера",
    "Курсы",
    "Бренд/учеба",
]


# Русские названия месяцев (сервер в английской локали)
RU_MONTHS = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}


@router.get("/api/ai/generate-milestones", response_class=HTMLResponse)
async def generate_milestones(request: Request, month: str = None, stage: str = "preview"):
    """Генератор Карьерного капитала: preview (быстрый поиск) -> analyze (AI).
    
    stage=preview: быстрый SQL-поиск, возвращает прогресс с автотриггером на analyze.
    stage=analyze: AI-анализ + сохранение + финальный результат.
    """
    today = date.today()

    if month:
        try:
            year, mon = month.split("-")
            target_month = date(int(year), int(mon), 1)
        except (ValueError, TypeError):
            target_month = today.replace(day=1)
    else:
        target_month = today.replace(day=1)

    # Последний день месяца
    if target_month.month == 12:
        last_day = target_month.replace(year=target_month.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        last_day = target_month.replace(month=target_month.month + 1, day=1) - timedelta(days=1)

    period_str = target_month.strftime("%Y-%m")
    month_label = f"{RU_MONTHS[target_month.month]} {target_month.year}"

    async with async_session() as db:
        # Находим ID карьерных категорий
        cat_result = await db.execute(
            select(Category.id, Category.name).where(Category.name.in_(CAREER_CATEGORIES))
        )
        career_cat_ids = {row[0]: row[1] for row in cat_result.all()}

        if not career_cat_ids:
            return HTMLResponse(
                '<div class="p-4 bg-yellow-900/20 text-yellow-500 rounded-lg">Не найдены карьерные категории.</div>'
            )

        # Берём ВСЕ выполненные задачи за месяц из карьерных категорий
        result = await db.execute(
            select(Task)
            .options(selectinload(Task.category))
            .where(
                Task.status == "выполнена",
                Task.completed_at >= target_month,
                Task.completed_at <= last_day + timedelta(days=1),
                Task.category_id.in_(career_cat_ids.keys()),
            )
            .order_by(Task.completed_at.desc())
        )
        tasks = result.scalars().all()

        if not tasks:
            return HTMLResponse(
                f'<div class="p-4 bg-yellow-900/20 text-yellow-500 rounded-lg">'
                f'За {month_label} нет выполненных задач в карьерных категориях.'
                f'</div>'
            )

        # Убираем задачи, для которых уже есть impact (дедупликация)
        from app.models.impact import CareerImpact

        existing_result = await db.execute(
            select(CareerImpact.original_title).where(
                CareerImpact.period_month == period_str
            )
        )
        existing_titles = {row[0] for row in existing_result.all()}

        new_tasks = [t for t in tasks if t.title not in existing_titles]

        # === STAGE: PREVIEW ===
        if stage == "preview":
            if not new_tasks:
                return HTMLResponse(
                    f'<div class="p-4 bg-blue-900/20 text-blue-400 rounded-lg">'
                    f'Все {len(tasks)} задач за {month_label} уже проанализированы.'
                    f'</div>'
                )

            # Собираем сводку по категориям
            cat_counts = {}
            for t in new_tasks:
                cname = t.category.name if t.category else "Без категории"
                cat_counts[cname] = cat_counts.get(cname, 0) + 1

            cat_summary = ", ".join(
                f"{name}: {count}" for name, count in sorted(cat_counts.items(), key=lambda x: -x[1])
            )

            return HTMLResponse(f"""
                <div class="bg-dark-900/50 border border-dark-700 rounded-xl p-5">
                    <div class="flex items-center gap-3 mb-4">
                        <svg class="animate-spin h-5 w-5 text-accent" viewBox="0 0 24 24">
                            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none"/>
                            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                        </svg>
                        <p class="text-gray-200 font-bold">Анализирую {month_label}</p>
                    </div>

                    <div class="space-y-2 mb-3">
                        <p class="text-sm text-gray-400">
                            <span class="text-green-400 font-bold">&checkmark;</span>
                            Найдено <span class="text-white font-bold">{len(tasks)}</span> выполненных задач
                            в карьерных категориях
                        </p>
                        <p class="text-sm text-gray-400">
                            <span class="text-green-400 font-bold">&checkmark;</span>
                            Новых (ещё не проанализированных): <span class="text-white font-bold">{len(new_tasks)}</span>
                        </p>
                        <p class="text-sm text-gray-500">
                            Уже проанализировано ранее: {len(tasks) - len(new_tasks)}
                        </p>
                    </div>

                    <p class="text-xs text-gray-600 mb-3">По категориям: {cat_summary}</p>

                    <div class="flex items-center gap-2 text-sm text-gray-400">
                        <svg class="animate-spin h-4 w-4 text-accent" viewBox="0 0 24 24">
                            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none"/>
                            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                        </svg>
                        <span>AI анализирует {len(new_tasks)} задач и формулирует достижения...</span>
                    </div>

                    <div hx-get="/api/ai/generate-milestones?stage=analyze&month={period_str}"
                         hx-trigger="load delay:300ms"
                         hx-target="#impact-results"
                         hx-swap="innerHTML"></div>
                </div>
            """)

        # === STAGE: ANALYZE ===
        if not new_tasks:
            return HTMLResponse(
                f'<div class="p-4 bg-blue-900/20 text-blue-400 rounded-lg">'
                f'Все {len(tasks)} задач за {month_label} уже проанализированы.'
                f'</div>'
            )

        # Готовим данные для AI
        tasks_data = [
            {
                "title": t.title,
                "category": t.category.name if t.category else "Без категории",
                "description": t.description or "",
            }
            for t in new_tasks
        ]

        # Контекст Веры для AI
        vera_context = (
            "Вера Осолодкина — продукт-менеджер и основатель. "
            "Строит AI-инструменты: планировщик задач (FastAPI + Docker), "
            "TG-бота аналитики менеджмента. Технический бэкграунд, "
            "работает с AI API (Groq, DeepSeek), серверной инфраструктурой. "
            "Хочет находить и формулировать достижения для усиления резюме "
            "в направлении AI Product Management / Technical Product Management "
            "(НЕ SEO, НЕ маркетинг). Важны: запуск продуктов, автоматизация, "
            "работа с командой, тендеры, технические решения, AI-интеграции."
        )

        # Отправляем на анализ
        impacts = await ai_service.generate_impact_report(tasks_data, vera_context)

        # Stop-slop: чистим каждое достижение
        from app.services.ai_service import _stop_slop
        for item in impacts:
            if item.get('impact'):
                item['impact'] = await _stop_slop(item['impact'])

        if not impacts:
            return HTMLResponse(
                f'<div class="p-4 bg-yellow-900/20 text-yellow-500 rounded-lg">'
                f'AI проанализировал {len(new_tasks)} задач, но не нашёл значимых для карьерного капитала. '
                f'Попробуйте другой месяц.'
                f'</div>'
            )

        # Сохраняем в базу
        saved_count = 0
        for item in impacts:
            impact_obj = CareerImpact(
                original_title=item.get("original_title"),
                impact_description=item.get("impact"),
                category_name=item.get("category"),
                period_month=period_str,
            )
            db.add(impact_obj)
            saved_count += 1

        await db.commit()

    # Показываем результат
    months = []
    for i in range(5, -1, -1):
        d = today.replace(day=1) - timedelta(days=1)
        d = d.replace(day=1)
        if i > 0:
            for _ in range(i - 1):
                if d.month == 1:
                    d = d.replace(year=d.year - 1, month=12)
                else:
                    d = d.replace(month=d.month - 1)
        months.append(d)

    month_options = "".join(
        f'<option value="{m.strftime("%Y-%m")}" {"selected" if m == target_month else ""}>'
        f'{RU_MONTHS[m.month]} {m.year}</option>'
        for m in months
    )

    return HTMLResponse(f"""
        <div class="bg-green-900/20 border border-green-500/50 p-6 rounded-xl">
            <p class="text-green-400 font-bold mb-3 text-lg">Карьерный капитал пополнен</p>
            <p class="text-gray-300 text-sm mb-2">
                Проанализировано <span class="text-white font-bold">{len(new_tasks)}</span> новых задач
                из карьерных категорий за {month_label}.
            </p>
            <p class="text-gray-300 text-sm mb-4">
                Найдено достижений: <span class="text-accent font-bold">{saved_count}</span>.
                Пропущено (уже были): <span class="text-gray-500">{len(tasks) - len(new_tasks)}</span>.
            </p>

            <details class="mt-4">
                <summary class="text-sm text-gray-400 cursor-pointer hover:text-gray-300">
                    Анализировать другой месяц
                </summary>
                <div class="mt-3 flex gap-2">
                    <select id="career-month-select" class="bg-dark-700 border border-dark-500 rounded-lg px-3 py-1.5 text-white text-sm">
                        {month_options}
                    </select>
                    <button onclick="document.getElementById('analyze-btn').setAttribute('hx-vals',
                        JSON.stringify({{month: document.getElementById('career-month-select').value}}));
                        htmx.trigger('#analyze-btn', 'click')"
                            class="px-3 py-1.5 bg-dark-600 hover:bg-dark-500 text-gray-300 text-sm rounded-lg border border-dark-500 transition-all">
                        Переанализировать
                    </button>
                </div>
            </details>

            <button id="analyze-btn"
                    hx-get="/api/ai/generate-milestones"
                    hx-target="#impact-results"
                    class="hidden"></button>

            <button onclick="window.location.reload()"
                    class="mt-4 px-4 py-2 bg-accent text-white rounded-lg font-bold shadow-lg">
                Обновить и посмотреть
            </button>
        </div>
    """)


@router.get("/api/career/export", response_class=HTMLResponse)
async def export_career_capital(request: Request):
    """Экспорт всех достижений в Markdown."""
    async with async_session() as db:
        from app.models.impact import CareerImpact

        result = await db.execute(
            select(CareerImpact).order_by(
                CareerImpact.period_month.desc(), CareerImpact.created_at.desc()
            )
        )
        impacts = result.scalars().all()

        if not impacts:
            return HTMLResponse("Нет данных для экспорта.")

        md_content = "# Карьерный капитал — Вера Осолодкина\n\n"
        md_content += "## Профиль\n"
        md_content += "Продукт-менеджер / основатель. AI Product Management, технические продукты.\n\n"
        current_month = ""

        for imp in impacts:
            if imp.period_month != current_month:
                current_month = imp.period_month
                md_content += f"\n## {current_month}\n"
            md_content += f"### {imp.category_name}\n"
            md_content += f"- **{imp.impact_description}**\n"
            md_content += f"  *(из задачи: {imp.original_title})*\n"

        from fastapi.responses import Response

        return Response(
            content=md_content,
            media_type="text/markdown",
            headers={
                "Content-Disposition": f"attachment; filename=career_capital_{date.today().isoformat()}.md"
            },
        )
