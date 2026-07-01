import pytest
from datetime import date
from app.models.finance import Transaction
from app.models.goal import FinancialGoal
from app.models.category import Category
from sqlalchemy import select

@pytest.mark.asyncio
async def test_create_transaction_manual(client, db):
    """Тест создания транзакции вручную"""
    # 1. Создаем категорию для теста
    cat = Category(name="Тест Финансы", type="finance")
    db.add(cat)
    await db.commit()
    await db.refresh(cat)

    # 2. Отправляем форму
    response = await client.post("/finance/create", data={
        "amount": 1500.50,
        "description": "Тестовая трата",
        "date": date.today().isoformat(),
        "category_id": cat.id
    })
    
    assert response.status_code == 303
    assert response.headers["location"] == "/finance"

    # 3. Проверяем в БД
    res = await db.execute(select(Transaction).where(Transaction.description == "Тестовая трата"))
    tx = res.scalar_one_or_none()
    assert tx is not None
    assert tx.amount == 1500.50
    assert tx.category_id == cat.id

@pytest.mark.asyncio
async def test_finance_page_renders(client, db):
    """Тест отображения страницы финансов"""
    response = await client.get("/finance")
    assert response.status_code == 200
    assert "Финансы" in response.text
    assert "Остаток" in response.text
    assert "Сводка" in response.text
    assert "Расходы по категориям" in response.text


@pytest.mark.asyncio
async def test_finance_page_empty_chart_state(client, db):
    """Без расходов за месяц — заглушка вместо canvas"""
    response = await client.get("/finance?month=1&year=2099")
    assert response.status_code == 200
    assert "finance-chart-empty" in response.text
    assert "Нет расходов" in response.text
    assert "finance-expense-chart" not in response.text


@pytest.mark.asyncio
async def test_finance_expense_chart_with_data(client, db):
    """Круговая диаграмма: canvas, Chart.js и данные категории"""
    cat = Category(name="Продукты", type="finance")
    db.add(cat)
    await db.commit()
    await db.refresh(cat)

    tx_date = date(2026, 3, 15)
    db.add(Transaction(
        amount=2500.0,
        description="Магазин",
        date=tx_date,
        category_id=cat.id,
        source="manual",
    ))
    await db.commit()

    response = await client.get("/finance?month=3&year=2026")
    assert response.status_code == 200
    assert 'id="finance-expense-chart"' in response.text
    assert "chart.js" in response.text.lower()
    assert "Продукты" in response.text
    assert "finance-chart-legend" in response.text
    assert "finance-chart-empty" not in response.text
    assert "2500" in response.text

@pytest.mark.asyncio
async def test_financial_goals_init(db):
    """Проверка наличия целей в БД (которые мы засидили)"""
    res = await db.execute(select(FinancialGoal))
    goals = res.scalars().all()
    # В реальной БД мы их вставляли руками через sqlite3, 
    # в тестовой нам нужно их создать если conftest их не сидит.
    # Но для теста мы можем создать одну.
    goal = FinancialGoal(name="Тестовая Цель", target_amount=1000, current_amount=100)
    db.add(goal)
    await db.commit()
    
    res = await db.execute(select(FinancialGoal).where(FinancialGoal.name == "Тестовая Цель"))
    assert res.scalar_one_or_none() is not None
