from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from sqlalchemy import select, func, delete, Table, MetaData, Column, Integer, String, Float, Date, DateTime, ForeignKey
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
from app.models.goal_history import GoalHistory
from app.config import settings

# Reflected tables for investment analytics
investment_snapshots = Table(
    'investment_snapshots', MetaData(),
    Column('id', Integer),
    Column('goal_id', Integer),
    Column('date', Date),
    Column('total_balance', Float),
)

investment_flows = Table(
    'investment_flows', MetaData(),
    Column('id', Integer),
    Column('goal_id', Integer),
    Column('date', Date),
    Column('type', String),
    Column('amount', Float),
    Column('description', String),
)

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


# Sparkline helper for Jinja2
def sparkline(history):
    if not history or len(history) < 2:
        return ""
    vals = [h["amount"] for h in history]
    mn = min(vals) * 0.98
    mx = max(vals) * 1.02
    rng = mx - mn or 1
    h, w = 24, 100
    step = w / (len(vals) - 1)
    pts = []
    for i, v in enumerate(vals):
        x = i * step
        y = h - ((v - mn) / rng) * h
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)

router = APIRouter()

import datetime as dt
from sqlalchemy import desc, case
from app.models.goal import FinancialGoal
from app.models.portfolio import Portfolio

# ID категорий-сбережений (не считаются расходами)
SAVINGS_CATEGORY_IDS = [37, 61, 157]  # ИИС, Подушка, Переводы между счетами

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
        year_groups = {}
        for row in available_months:
            y, m = int(row.year), int(row.month)
            tab = {"month": m, "year": y, "name": f"{MONTH_NAMES[m]}"}
            month_tabs.append(tab)
            year_groups.setdefault(y, []).append(tab)
        # Sort months within each year chronologically (Jan left, Dec right)
        for y in year_groups:
            year_groups[y].sort(key=lambda t: t["month"])
        year_groups = dict(sorted(year_groups.items(), reverse=True))
            
        if not month_tabs:
            month_tabs.append({"month": today.month, "year": today.year, "name": f"{MONTH_NAMES[today.month]}"})
            year_groups = {today.year: month_tabs}

        view_month = month or month_tabs[0]["month"]
        view_year = year or month_tabs[0]["year"]
        
        start_date = dt.date(view_year, view_month, 1)
        if view_month == 12:
            end_date = dt.date(view_year + 1, 1, 1)
        else:
            end_date = dt.date(view_year, view_month + 1, 1)

        # Транзакции за месяц (исключая переводы между своими счетами)
        result = await db.execute(
            select(Transaction)
            .options(selectinload(Transaction.category).selectinload(Category.parent))
            .where(Transaction.date >= start_date, Transaction.date < end_date)
            .order_by(Transaction.date.desc())
        )
        transactions = result.scalars().all()
        
        # Вычисляем parent-строку пока сессия жива (для Jinja2)
        for tx in transactions:
            if tx.category and tx.category.parent:
                tx._cat_parent = tx.category.parent.name
            else:
                tx._cat_parent = ''
        
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

        portfolios_res = await db.execute(
            select(Portfolio).order_by(Portfolio.sort_order)
        )
        portfolios = portfolios_res.scalars().all()
        portfolio_slugs = {
            p.legacy_goal_id: p.slug
            for p in portfolios
            if p.legacy_goal_id is not None
        }
        reserve_goal_ids = {
            p.legacy_goal_id
            for p in portfolios
            if p.type == "reserve" and p.legacy_goal_id is not None
        }
        goals_by_id = {g.id: g for g in goals}
        investment_goals = [
            goals_by_id[gid]
            for p in portfolios
            if (gid := p.legacy_goal_id) is not None and gid in goals_by_id
        ]
        investment_goal_ids = set(portfolio_slugs.keys())
        other_goals = [g for g in goals if g.id not in investment_goal_ids]

        def is_completed(g):
            if g.id in reserve_goal_ids:
                return False
            return g.target_amount > 0 and g.current_amount >= g.target_amount

        active_investment = [g for g in investment_goals if not is_completed(g)]
        completed_investment = [g for g in investment_goals if is_completed(g)]
        active_other = [g for g in other_goals if not is_completed(g)]
        completed_other = [g for g in other_goals if is_completed(g)]
        completed_count = len(completed_investment) + len(completed_other)
        
        # Reassign to active-only for template context
        investment_goals = active_investment
        other_goals = active_other
        
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
        
        # Сбережения за год (из investment_flows: deposit - withdrawal, только положительные)
        inv_savings_res = await db.execute(
            select(
                investment_flows.c.goal_id,
                func.sum(case((investment_flows.c.type == 'deposit', investment_flows.c.amount), else_=0)).label('dep'),
                func.sum(case((investment_flows.c.type == 'withdrawal', func.abs(investment_flows.c.amount)), else_=0)).label('wdr')
            )
            .where(
                investment_flows.c.date >= year_start,
                investment_flows.c.date < year_end
            )
            .group_by(investment_flows.c.goal_id)
        )
        yearly_savings = 0
        for row in inv_savings_res:
            net = (row.dep or 0) - (row.wdr or 0)
            if net > 0:
                yearly_savings += net
        yearly_savings = round(yearly_savings)
        yearly_expense = yearly_expense_gross

        # Month stats for previous years (same month, different year)
        prev_years = []
        for py in [view_year - 1, view_year - 2]:
            py_start = dt.date(py, view_month, 1)
            if view_month == 12:
                py_end = dt.date(py + 1, 1, 1)
            else:
                py_end = dt.date(py, view_month + 1, 1)
            py_res = await db.execute(
                select(
                    func.sum(case((Transaction.amount > 0, Transaction.amount), else_=0)).label('income'),
                    func.sum(case((Transaction.amount < 0, func.abs(Transaction.amount)), else_=0)).label('expense')
                )
                .where(Transaction.date >= py_start, Transaction.date < py_end)
            )
            py_row = py_res.first()
            py_income = py_row.income or 0
            py_expense_gross = py_row.expense or 0
            py_inv_res = await db.execute(
                select(
                    investment_flows.c.goal_id,
                    func.sum(case((investment_flows.c.type == 'deposit', investment_flows.c.amount), else_=0)).label('dep'),
                    func.sum(case((investment_flows.c.type == 'withdrawal', func.abs(investment_flows.c.amount)), else_=0)).label('wdr')
                )
                .where(investment_flows.c.date >= py_start, investment_flows.c.date < py_end)
                .group_by(investment_flows.c.goal_id)
            )
            py_savings = 0
            for row in py_inv_res:
                net = (row.dep or 0) - (row.wdr or 0)
                if net > 0:
                    py_savings += net
            py_savings = round(py_savings)
            if py_income > 0 or py_expense_gross > 0:
                prev_years.append({
                    'year': py,
                    'income': py_income,
                    'expense': py_expense_gross - py_savings,
                    'savings': py_savings
                })

        # Full-year stats for previous years
        full_years = []
        for fy in [view_year - 1, view_year - 2]:
            fy_start = dt.date(fy, 1, 1)
            fy_end = dt.date(fy + 1, 1, 1)
            fy_res = await db.execute(
                select(
                    func.sum(case((Transaction.amount > 0, Transaction.amount), else_=0)).label('income'),
                    func.sum(case((Transaction.amount < 0, func.abs(Transaction.amount)), else_=0)).label('expense')
                ).where(Transaction.date >= fy_start, Transaction.date < fy_end)
            )
            fy_row = fy_res.first()
            fy_income = fy_row.income or 0
            fy_expense_gross = fy_row.expense or 0
            fy_inv_res = await db.execute(
                select(
                    investment_flows.c.goal_id,
                    func.sum(case((investment_flows.c.type == 'deposit', investment_flows.c.amount), else_=0)).label('dep'),
                    func.sum(case((investment_flows.c.type == 'withdrawal', func.abs(investment_flows.c.amount)), else_=0)).label('wdr')
                )
                .where(investment_flows.c.date >= fy_start, investment_flows.c.date < fy_end)
                .group_by(investment_flows.c.goal_id)
            )
            fy_savings = 0
            for row in fy_inv_res:
                net = (row.dep or 0) - (row.wdr or 0)
                if net > 0:
                    fy_savings += net
            fy_savings = round(fy_savings)
            if fy_income > 0 or fy_expense_gross > 0:
                full_years.append({
                    'year': fy,
                    'income': fy_income,
                    'expense': fy_expense_gross - fy_savings,
                    'savings': fy_savings
                })

        # РАЗБИВКА ПО ДНЯМ (как в сводке импорта Гермеса: День | Доход | Расход | Операций)
        MONTH_ABBR = {
            1: "янв", 2: "фев", 3: "мар", 4: "апр", 5: "май", 6: "июн",
            7: "июл", 8: "авг", 9: "сен", 10: "окт", 11: "ноя", 12: "дек",
        }
        WEEKDAY_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
        daily_map: dict = {}
        for tx in transactions:
            entry = daily_map.setdefault(tx.date, {"income": 0.0, "expense": 0.0, "count": 0})
            if tx.amount > 0:
                entry["income"] += tx.amount
            elif tx.category_id not in SAVINGS_CATEGORY_IDS:
                entry["expense"] += abs(tx.amount)
            entry["count"] += 1

        last_day = end_date - timedelta(days=1)
        if view_year == today.year and view_month == today.month:
            last_day = today

        daily_breakdown = []
        d_cursor = start_date
        while d_cursor <= last_day:
            entry = daily_map.get(d_cursor, {"income": 0.0, "expense": 0.0, "count": 0})
            wd = d_cursor.weekday()
            is_weekend = wd >= 5
            daily_breakdown.append({
                "date": d_cursor,
                "label": f"{d_cursor.day} {MONTH_ABBR[d_cursor.month]}",
                "weekday": WEEKDAY_SHORT[wd],
                "income": entry["income"],
                "expense": entry["expense"],
                "count": entry["count"],
                "is_today": d_cursor == today,
                "is_weekend": is_weekend,
            })
            d_cursor += timedelta(days=1)

        daily_totals = {
            "income": sum(day["income"] for day in daily_breakdown),
            "expense": sum(day["expense"] for day in daily_breakdown),
            "count": sum(day["count"] for day in daily_breakdown),
        }

        # Расчет итогов за месяц
        total_income = sum(tx.amount for tx in transactions if tx.amount > 0)
        total_expense = sum(abs(tx.amount) for tx in transactions if tx.amount < 0 and tx.category_id not in SAVINGS_CATEGORY_IDS)
        # Месячные сбережения (из investment_flows)
        month_inv_res = await db.execute(
            select(
                investment_flows.c.goal_id,
                func.sum(case((investment_flows.c.type == 'deposit', investment_flows.c.amount), else_=0)).label('dep'),
                func.sum(case((investment_flows.c.type == 'withdrawal', func.abs(investment_flows.c.amount)), else_=0)).label('wdr')
            )
            .where(investment_flows.c.date >= start_date, investment_flows.c.date < end_date)
            .group_by(investment_flows.c.goal_id)
        )
        total_savings = 0
        for row in month_inv_res:
            net = (row.dep or 0) - (row.wdr or 0)
            if net > 0:
                total_savings += net
        total_savings = round(total_savings)
        
        # Список категорий для модалки (иерархия)
        goal_ids = [g.id for g in goals]
        goal_history = {}
        if goal_ids:
            hist_res = await db.execute(
                select(GoalHistory)
                .where(GoalHistory.goal_id.in_(goal_ids))
                .order_by(GoalHistory.created_at.asc())
            )
            for h in hist_res.scalars().all():
                goal_history.setdefault(h.goal_id, []).append({
                    "date": h.created_at.strftime("%Y-%m-%d") if h.created_at else "",
                    "amount": h.new_amount,
                    "delta": h.delta,
                    "note": h.note
                })

        # Calculate % change using investment_snapshots (real broker data)
        now = datetime.now()
        month_ago = now - timedelta(days=30)
        year_ago = now - timedelta(days=365)
        goal_changes = {}
        snapshots_res = await db.execute(
            select(investment_snapshots.c.goal_id, investment_snapshots.c.date, investment_snapshots.c.total_balance)
            .order_by(investment_snapshots.c.date.asc())
        )
        snapshots_by_goal = {}
        for row in snapshots_res:
            snapshots_by_goal.setdefault(row.goal_id, []).append((row.date, row.total_balance))
        
        for g in goals:
            snaps = snapshots_by_goal.get(g.id, [])
            changes = {"month": None, "year": None}
            if len(snaps) >= 2:
                # Month: find closest snapshot <= month_ago, compare to latest
                month_snap = None
                for d, bal in snaps:
                    if d <= month_ago.date():
                        month_snap = bal
                    else:
                        break
                latest = snaps[-1][1]
                if month_snap and month_snap > 0 and latest != month_snap:
                    changes["month"] = round((latest - month_snap) / month_snap * 100, 1)
                # Year
                year_snap = None
                for d, bal in snaps:
                    if d <= year_ago.date():
                        year_snap = bal
                    else:
                        break
                if year_snap and year_snap > 0 and latest != year_snap:
                    changes["year"] = round((latest - year_snap) / year_snap * 100, 1)
            goal_changes[g.id] = changes

        fin_cats_res = await db.execute(select(Category).where(Category.type == 'finance').order_by(Category.name))
        fin_categories_all = fin_cats_res.scalars().all()
        fin_categories = fin_categories_all
        fin_parent_cats = [c for c in fin_categories_all if not c.parent_id]
        fin_sub_cats_by_parent: dict = {}
        for c in fin_categories_all:
            if c.parent_id:
                fin_sub_cats_by_parent.setdefault(c.parent_id, []).append(c)
        
    response = templates.TemplateResponse(request, "finance.html", {
        "request": request,
        "transactions": transactions,
        "daily_breakdown": daily_breakdown,
        "daily_totals": daily_totals,
        "grouped_summary": grouped_summary,
        "category_summary": category_summary,
        "chart_expense_total": chart_expense_total,
        "goal_history": goal_history,
        "goal_changes": goal_changes,
        "sparkline": sparkline,
        "goals": goals,
        "investment_goals": investment_goals,
        "portfolio_slugs": portfolio_slugs,
        "other_goals": other_goals,
        "completed_investment": completed_investment,
        "completed_other": completed_other,
        "completed_count": completed_count,
        "prev_years": prev_years,
        "full_years": full_years,
        "yearly_stats": {
            "income": yearly_income, 
            "expense": yearly_expense, 
            "savings": yearly_savings
        },
        "month_tabs": month_tabs,
        "year_groups": year_groups,
        "current_month": view_month,
        "current_year": view_year,
        "fin_categories": fin_categories,
        "fin_parent_cats": fin_parent_cats,
        "fin_sub_cats_by_parent": fin_sub_cats_by_parent,
        "monthly_savings": total_savings,
        "stats": {
            "income": total_income, 
            "expense": total_expense, 
            "savings": total_savings,
            "balance": total_income - total_expense - total_savings
        },
        "month_name": f"{MONTH_NAMES[view_month]} {view_year}",
        "month_short": MONTH_NAMES[view_month],
        "today": today
    })
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


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


# Goal History API
@router.post("/api/goals/{goal_id}/update")
async def update_goal_amount(goal_id: int, new_amount: float = Form(...)):
    async with async_session() as db:
        res = await db.execute(select(FinancialGoal).where(FinancialGoal.id == goal_id))
        goal = res.scalar_one_or_none()
        if not goal:
            return JSONResponse({"error": "not found"}, status_code=404)
        delta = new_amount - goal.current_amount
        goal.current_amount = new_amount
        hist = GoalHistory(goal_id=goal_id, new_amount=new_amount, delta=delta)
        db.add(hist)
        await db.commit()
    return JSONResponse({"ok": True, "new_amount": new_amount, "delta": delta})

@router.post("/api/goals/create")
async def create_goal(name: str = Form(...), target_amount: float = Form(...), current_amount: float = Form(0)):
    async with async_session() as db:
        goal = FinancialGoal(name=name, target_amount=target_amount, current_amount=current_amount)
        db.add(goal)
        await db.commit()
        await db.refresh(goal)
        if current_amount > 0:
            hist = GoalHistory(goal_id=goal.id, new_amount=current_amount, delta=current_amount)
            db.add(hist)
            await db.commit()
    return JSONResponse({"ok": True, "id": goal.id, "name": goal.name})

@router.get("/api/goals/{goal_id}/history")
async def get_goal_history(goal_id: int):
    async with async_session() as db:
        res = await db.execute(
            select(GoalHistory)
            .where(GoalHistory.goal_id == goal_id)
            .order_by(GoalHistory.created_at.asc())
            .limit(52)
        )
        rows = res.scalars().all()
        return JSONResponse([{
            "date": r.created_at.strftime("%Y-%m-%d"),
            "amount": r.new_amount,
            "delta": r.delta,
            "note": r.note
        } for r in rows])

@router.get("/api/goals/{goal_id}/analytics")
async def get_goal_analytics(goal_id: int, period: str = "all"):
    """Backward-compat alias: resolve portfolio by legacy goal_id."""
    from app.services.portfolio_service import (
        PortfolioNotFoundError,
        build_portfolio_analytics,
        get_portfolio_by_legacy_goal_id,
    )

    async with async_session() as db:
        portfolio = await get_portfolio_by_legacy_goal_id(db, goal_id)
        if portfolio is None:
            raise HTTPException(status_code=404, detail=f"No portfolio for goal {goal_id}")
        try:
            data = await build_portfolio_analytics(db, portfolio.id)
        except PortfolioNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        data["goal_id"] = goal_id
        return JSONResponse(data)
