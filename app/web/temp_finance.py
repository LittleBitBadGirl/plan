            headers={"Content-Disposition": f"attachment; filename=career_capital_{date.today().isoformat()}.md"}
        )

@router.get("/finance", response_class=HTMLResponse)
async def finance_page(request: Request, month: Optional[int] = None, year: Optional[int] = None):
    """Страница финансов (Excel-вид)"""
    today = date.today()
    from sqlalchemy import desc
    
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
            month_tabs.append({
                "month": m,
                "year": y,
                "name": f"{MONTH_NAMES[m]} {y}"
            })
            
        if not month_tabs:
            month_tabs.append({"month": today.month, "year": today.year, "name": f"{MONTH_NAMES[today.month]} {today.year}"})

        view_month = month or month_tabs[0]["month"]
        view_year = year or month_tabs[0]["year"]
        
        start_date = date(view_year, view_month, 1)
        if view_month == 12:
            end_date = date(view_year + 1, 1, 1)
        else:
            end_date = date(view_year, view_month + 1, 1)

        result = await db.execute(
            select(Transaction)
            .options(selectinload(Transaction.category))
            .where(Transaction.date >= start_date, Transaction.date < end_date)
            .order_by(Transaction.date.desc())
        )
        transactions = result.scalars().all()
        
        total_income = sum(abs(tx.amount) for tx in transactions if tx.amount < 0)
        total_expense = sum(tx.amount for tx in transactions if tx.amount > 0)
        balance = total_income - total_expense
        
        summary_result = await db.execute(
            select(Category.name, func.sum(Transaction.amount))
            .join(Transaction)
            .where(
                Transaction.date >= start_date, 
                Transaction.date < end_date,
                Transaction.amount > 0
            )
            .group_by(Category.name)
            .order_by(desc(func.sum(Transaction.amount)))
        )
        category_summary = summary_result.all()
            
    return templates.TemplateResponse(request, "finance.html", {
        "request": request,
        "transactions": transactions,
        "category_summary": category_summary,
        "month_tabs": month_tabs,
        "current_month": view_month,
        "current_year": view_year,
        "stats": {
            "income": total_income,
            "expense": total_expense,
            "balance": balance
        },
        "today": today
    })
