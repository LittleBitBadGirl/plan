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

import datetime as dt
from sqlalchemy import desc, case
from app.models.goal import FinancialGoal

# ID категорий-сбережений (не считаются расходами)
SAVINGS_CATEGORY_IDS = [37, 61]  # ИИС, Подушка

@router.get("/finance", response_class=HTMLResponse)
async def finance_page(request: Request, month: Optional[int] = None, year: Optional[int] = None):
    """Страница финансов (Excel-вид)"""
    today = dt.date.today()
    
    MONTH_NAMES = {
        1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
        5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
        9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
    }
    
    async with async_session() as db:
        available_months_res = await db.execute(
            select(
                func.strftime('%Y', Transaction.date).label('year'),
                func.strftime('%m', Transaction.date).label('month')
            )
            .group_by('year', 'month')
            .order_by(desc('year'), desc('month'))
        )
        available_months = available_months_res.all()
        
        month_tabs = []
        for row in available_months:
            y, m = int(row.year), int(row.month)
            month_tabs.append({"month": m, "year": y, "name": f"{MONTH_NAMES[m]} {y}"})
            
        if not month_tabs:
            month_tabs.append({"month": today.month, "year": today.year, "name": f"{MONTH_NAMES[today.month]} {today.year}"})

        view_month = month or month_tabs[0]["month"]
        view_year = year or month_tabs[0]["year"]
        
        start_date = dt.date(view_year, view_month, 1)
        if view_month == 12:
            end_date = dt.date(view_year + 1, 1, 1)
        else:
            end_date = dt.date(view_year, view_month + 1, 1)

        # Транзакции за месяц
        result = await db.execute(
            select(Transaction)
            .options(selectinload(Transaction.category).selectinload(Category.parent))
            .where(Transaction.date >= start_date, Transaction.date < end_date)
            .order_by(Transaction.date.desc())
        )
        transactions = result.scalars().all()
        
        # СВОДКА (Трехуровневая группировка: Месяц -> Глобальная Кат -> Подкат)
        # Исключаем сбережения из сводки расходов
        summary_result = await db.execute(
            select(
                Category.name.label('cat_name'),
                func.sum(Transaction.amount).label('total'),
                Category.parent_id
            )
            .join(Transaction)
            .where(
                Transaction.date >= start_date, 
                Transaction.date < end_date, 
                Transaction.amount < 0,
                Transaction.category_id.notin_(SAVINGS_CATEGORY_IDS)
            )
            .group_by(Category.id)
            .order_by(desc(func.sum(Transaction.amount)))
        )
        raw_summary = summary_result.all()
        
        # Группируем по родителям
        grouped_summary = {}
        parent_ids = list(set([r.parent_id for r in raw_summary if r.parent_id]))
        parents_res = await db.execute(select(Category).where(Category.id.in_(parent_ids)))
        parents_map = {p.id: p.name for p in parents_res.scalars().all()}

        for r in raw_summary:
            if r.parent_id:
                p_name = parents_map.get(r.parent_id, r.cat_name)
            else:
                p_name = r.cat_name

            if p_name not in grouped_summary:
                grouped_summary[p_name] = {"total": 0, "sub_items": []}
            if r.parent_id:
                grouped_summary[p_name]["sub_items"].append({"name": r.cat_name, "amount": abs(r.total)})
            grouped_summary[p_name]["total"] += abs(r.total)

        # Добавляем некатегоризированные расходы
        uncat_res = await db.execute(
            select(func.sum(Transaction.amount)).where(
                Transaction.date >= start_date,
                Transaction.date < end_date,
                Transaction.amount < 0,
                Transaction.category_id.is_(None),
            )
        )
        uncat_total = uncat_res.scalar() or 0
        if uncat_total < 0:
            grouped_summary["Без категории"] = {
                "total": abs(uncat_total),
                "sub_items": [{"name": "Нет категории", "amount": abs(uncat_total)}],
            }

        # Сортируем по убыванию суммы
        grouped_summary = dict(
            sorted(grouped_summary.items(), key=lambda x: x[1]["total"], reverse=True)
        )

        category_summary = [
            {"name": name, "amount": float(abs(data["total"]))}
            for name, data in grouped_summary.items()
        ]
        chart_expense_total = sum(item["amount"] for item in category_summary)

        # ЦЕЛИ
        goals_res = await db.execute(select(FinancialGoal))
        goals = goals_res.scalars().all()
        # Разделение: инвестиционные счета (ИИС, брокерские) vs обычные цели
        INVESTMENT_GOAL_IDS = [1, 6, 7]  # ИИС, Брокерский 1, Брокерский 2
        investment_goals = [g for g in goals if g.id in INVESTMENT_GOAL_IDS]
        other_goals = [g for g in goals if g.id not in INVESTMENT_GOAL_IDS]
        
        # ИТОГИ ГОДА (для view_year)
        year_start = dt.date(view_year, 1, 1)
        year_end = dt.date(view_year + 1, 1, 1)
        
        yearly_res = await db.execute(
            select(
                func.sum(case((Transaction.amount > 0, Transaction.amount), else_=0)).label('income'),
                func.sum(case((Transaction.amount < 0, func.abs(Transaction.amount)), else_=0)).label('expense')
            )
            .where(Transaction.date >= year_start, Transaction.date < year_end)
        )
        yearly_row = yearly_res.first()
        yearly_income = yearly_row.income or 0
        yearly_expense_gross = yearly_row.expense or 0
        
        # Сбережения за год
        yearly_savings_res = await db.execute(
            select(func.sum(func.abs(Transaction.amount)))
            .where(
                Transaction.date >= year_start, 
                Transaction.date < year_end, 
                Transaction.amount < 0,
                Transaction.category_id.in_(SAVINGS_CATEGORY_IDS)
            )
        )
        yearly_savings = yearly_savings_res.scalar() or 0
        yearly_expense = yearly_expense_gross - yearly_savings
        
        # Расчет итогов за месяц
        total_income = sum(tx.amount for tx in transactions if tx.amount > 0)
        total_expense = sum(abs(tx.amount) for tx in transactions if tx.amount < 0 and tx.category_id not in SAVINGS_CATEGORY_IDS)
        total_savings = sum(abs(tx.amount) for tx in transactions if tx.amount < 0 and tx.category_id in SAVINGS_CATEGORY_IDS)
        
        # Список категорий для модалки (иерархия)
        fin_cats_res = await db.execute(select(Category).where(Category.type == 'finance').order_by(Category.name))
        fin_categories_all = fin_cats_res.scalars().all()
        fin_categories = fin_categories_all
        fin_parent_cats = [c for c in fin_categories_all if not c.parent_id]
        fin_sub_cats_by_parent: dict = {}
        for c in fin_categories_all:
            if c.parent_id:
                fin_sub_cats_by_parent.setdefault(c.parent_id, []).append(c)
        
    return templates.TemplateResponse(request, "finance.html", {
        "request": request,
        "transactions": transactions,
        "grouped_summary": grouped_summary,
        "category_summary": category_summary,
        "chart_expense_total": chart_expense_total,
        "goals": goals,
        "investment_goals": investment_goals,
        "other_goals": other_goals,
        "yearly_stats": {
            "income": yearly_income, 
            "expense": yearly_expense, 
            "savings": yearly_savings
        },
        "month_tabs": month_tabs,
        "current_month": view_month,
        "current_year": view_year,
        "fin_categories": fin_categories,
        "fin_parent_cats": fin_parent_cats,
        "fin_sub_cats_by_parent": fin_sub_cats_by_parent,
        "stats": {
            "income": total_income, 
            "expense": total_expense, 
            "savings": total_savings,
            "balance": total_income - total_expense - total_savings
        },
        "month_name": f"{MONTH_NAMES[view_month]} {view_year}",
        "today": today
    })


@router.post("/finance/create")
async def create_transaction(
    amount: float = Form(...),
    description: str = Form(""),
    date: str = Form(...),
    category_id: Optional[int] = Form(None)
):
    """Создать транзакцию вручную"""
    from app.models.finance import Transaction
    from datetime import date as py_date
    async with async_session() as db:
        tx = Transaction(
            amount=amount,
            description=description,
            date=py_date.fromisoformat(date),
            category_id=category_id,
            source="manual"
        )
        db.add(tx)
        await db.commit()
    
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/finance", status_code=303)


@router.post("/api/transactions/{tx_id}/category")
async def update_transaction_category(
    tx_id: int,
    category_id: str = Form(""),
    apply_to_similar: bool = Form(False),
):
    """Сменить категорию транзакции inline.
    
    apply_to_similar=True обновит ВСЕ транзакции с тем же описанием.
    Это создаёт merchant-memory эффект без отдельной таблицы правил.
    """
    from fastapi.responses import JSONResponse
    from app.models.finance import Transaction as Tx

    cat_id = int(category_id) if category_id and category_id.isdigit() else None

    async with async_session() as db:
        res = await db.execute(select(Tx).where(Tx.id == tx_id))
        tx = res.scalar_one_or_none()
        if not tx:
            return JSONResponse({"error": "not found"}, status_code=404)

        description = tx.description
        tx.category_id = cat_id

        updated_count = 1
        if apply_to_similar and description:
            from sqlalchemy import update as sa_update
            result = await db.execute(
                sa_update(Tx)
                .where(Tx.description == description, Tx.id != tx_id)
                .values(category_id=cat_id)
            )
            updated_count += result.rowcount

        await db.commit()

        cat_name = "Прочее"
        if cat_id:
            cat_res = await db.execute(select(Category).where(Category.id == cat_id))
            cat = cat_res.scalar_one_or_none()
            if cat:
                cat_name = cat.name

    return JSONResponse({"category_name": cat_name, "updated": updated_count})
