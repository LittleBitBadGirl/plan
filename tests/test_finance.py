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
    assert "Доход" in response.text
    assert "Расход" in response.text
    assert "Остаток" not in response.text
    assert "Аналитика" in response.text


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
    """Круговая диаграмма: canvas, локальная библиотека и данные категории.

    Расход в проекте пишется ОТРИЦАТЕЛЬНОЙ суммой (сводка расходов фильтрует
    amount < 0, так же приходят выписки банка). Раньше тест клал +2500 и ждал
    диаграмму расходов — то есть проверял то, чего быть не может, и падал.
    """
    cat = Category(name="Продукты", type="finance")
    db.add(cat)
    await db.commit()
    await db.refresh(cat)

    tx_date = date(2026, 3, 15)
    db.add(Transaction(
        amount=-2500.0,
        description="Магазин",
        date=tx_date,
        category_id=cat.id,
        source="manual",
    ))
    await db.commit()

    response = await client.get("/finance?month=3&year=2026")
    assert response.status_code == 200
    assert 'id="finance-expense-chart"' in response.text
    # библиотека графиков лежит в проекте, а не тянется с CDN
    assert "chart.umd.min.js" in response.text
    assert "Продукты" in response.text
    assert "finance-chart-legend" in response.text
    assert "finance-chart-empty" not in response.text
    # сумма доехала именно в данные диаграммы, а не просто мелькнула на странице
    assert "data-chart-amounts='[2500.0]'" in response.text

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


@pytest.mark.asyncio
async def test_other_goal_ok_button_saves_amount(client, db):
    """OK на карточке цели без портфеля должен вызывать saveGoal без битого @click."""
    goal = FinancialGoal(name="Зимовка", target_amount=200_000, current_amount=20_000)
    db.add(goal)
    await db.commit()
    await db.refresh(goal)
    goal_id = goal.id

    page = await client.get("/finance")
    assert page.status_code == 200
    assert f'@click="saveGoal({goal_id})" :disabled="saving"' in page.text
    assert f'saveGoal({goal_id})\\"' not in page.text

    response = await client.post(
        f"/api/goals/{goal_id}/update",
        data={"new_amount": 40_000},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True

    db.expire_all()
    saved = (
        await db.execute(select(FinancialGoal).where(FinancialGoal.id == goal_id))
    ).scalar_one()
    assert saved.current_amount == 40_000


@pytest.mark.asyncio
async def test_finance_category_modal_lists_transactions(client, db):
    """Попап «Аналитики»: транзакции глобальной категории за месяц, с подкатегориями."""
    # id задаём явно: тестовая база сидится категориями, и автоинкремент может
    # выдать 36/37/61 — id сбережений, которые роут законно отбрасывает.
    parent = Category(id=9001, name="Попап Родитель", type="finance", is_global=True)
    db.add(parent)
    await db.commit()
    await db.refresh(parent)
    sub = Category(id=9002, name="Попап Подкат", type="finance", parent_id=parent.id, is_global=False)
    db.add(sub)
    await db.commit()
    await db.refresh(sub)

    db.add_all([
        Transaction(date=date(2031, 3, 5), amount=-1200.0, description="Пятёрочка", category_id=sub.id),
        Transaction(date=date(2031, 3, 7), amount=-300.0, description="Магнит", category_id=parent.id),
        Transaction(date=date(2031, 3, 9), amount=-900.0, description="Чужая категория", category_id=None),
        Transaction(date=date(2031, 4, 2), amount=-500.0, description="Следующий месяц", category_id=sub.id),
    ])
    await db.commit()

    r = await client.get("/finance/category", params={"name": "Попап Родитель", "month": 3, "year": 2031})
    assert r.status_code == 200
    assert "Попап Родитель" in r.text
    assert "Пятёрочка" in r.text and "Магнит" in r.text
    assert "Попап Подкат" in r.text          # подкатегория видна в строке
    assert "2 операции" in r.text            # только март и только эта категория
    assert "1 500 ₽" in r.text               # 1200 + 300
    assert "Чужая категория" not in r.text
    assert "Следующий месяц" not in r.text


@pytest.mark.asyncio
async def test_finance_category_modal_uncategorized(client, db):
    """Группа «Без категории» открывается и показывает строки без category_id."""
    db.add(Transaction(date=date(2031, 5, 4), amount=-777.0, description="Бесхозная трата", category_id=None))
    await db.commit()

    r = await client.get("/finance/category", params={"name": "Без категории", "month": 5, "year": 2031})
    assert r.status_code == 200
    assert "Бесхозная трата" in r.text
    assert "Нет категории" in r.text


@pytest.mark.asyncio
async def test_finance_category_modal_empty_month(client, db):
    """Пустой месяц — понятная заглушка, а не пустая таблица."""
    r = await client.get("/finance/category", params={"name": "Попап Родитель", "month": 6, "year": 2031})
    assert r.status_code == 200
    assert "расходов в этой категории нет" in r.text
    assert "За июнь 2031" in r.text          # в шапке видно, за какой месяц смотрим


@pytest.mark.asyncio
async def test_finance_page_has_category_modal(client, db):
    """Страница содержит контейнер попапа и передаёт месяц/год диаграмме."""
    db.add(Transaction(date=date(2031, 7, 3), amount=-410.0, description="Для диаграммы", category_id=None))
    await db.commit()

    r = await client.get("/finance?month=7&year=2031")
    assert r.status_code == 200
    assert "finance-cat-modal" in r.text
    assert 'data-chart-month="7"' in r.text


def test_expense_group_name_rules():
    """Правило группировки: сирота не приклеивается к «Без категории».

    Сводка страницы собирается INNER JOIN и строки с category_id на удалённую
    категорию не видит. Если попап отнесёт их к «Без категории», его сумма
    разойдётся с сегментом диаграммы.
    """
    from types import SimpleNamespace
    from app.web.routes.finance import expense_group_name

    orphan = SimpleNamespace(category_id=9999, category=None)
    assert expense_group_name(orphan) is None

    uncategorized = SimpleNamespace(category_id=None, category=None)
    assert expense_group_name(uncategorized) == ("Без категории", "Нет категории")

    parent = SimpleNamespace(name="Еда")
    global_cat = SimpleNamespace(category_id=5, category=SimpleNamespace(name="Еда", parent=None))
    assert expense_group_name(global_cat) == ("Еда", "Еда")

    sub = SimpleNamespace(category_id=6, category=SimpleNamespace(name="Кофе", parent=parent))
    assert expense_group_name(sub) == ("Еда", "Кофе")


@pytest.mark.asyncio
async def test_finance_popup_matches_chart_sum(client, db):
    """Сумма попапа = сумма сегмента диаграммы, даже когда рядом лежит сирота."""
    import json as _json
    import re as _re

    parent = Category(id=9101, name="Паритет Родитель", type="finance", is_global=True)
    db.add(parent)
    await db.commit()
    sub = Category(id=9102, name="Паритет Подкат", type="finance", parent_id=9101, is_global=False)
    db.add(sub)
    await db.commit()

    db.add_all([
        Transaction(date=date(2032, 2, 3), amount=-1000.0, description="Подкат", category_id=9102),
        Transaction(date=date(2032, 2, 4), amount=-500.0, description="Сам родитель", category_id=9101),
        # сирота: категории с таким id нет — страница её не видит, попап тоже не должен
        Transaction(date=date(2032, 2, 5), amount=-7000.0, description="Сирота", category_id=9999),
        Transaction(date=date(2032, 2, 6), amount=-900.0, description="Бесхозная", category_id=None),
    ])
    await db.commit()

    page = await client.get("/finance?month=2&year=2032")
    assert page.status_code == 200
    labels = _json.loads(_re.search(r"data-chart-labels='([^']*)'", page.text).group(1))
    amounts = _json.loads(_re.search(r"data-chart-amounts='([^']*)'", page.text).group(1))
    chart_total = dict(zip(labels, amounts))["Паритет Родитель"]
    assert chart_total == 1500.0

    popup = await client.get("/finance/category", params={"name": "Паритет Родитель", "month": 2, "year": 2032})
    assert popup.status_code == 200
    assert "1 500 ₽" in popup.text
    assert "2 операции" in popup.text
    assert "Сирота" not in popup.text


@pytest.mark.asyncio
async def test_finance_popup_december_boundary(client, db):
    """Месяц 12: конец периода — 1 января следующего года, а не 12-е число."""
    db.add(Transaction(date=date(2032, 12, 31), amount=-321.0, description="Последний день", category_id=None))
    await db.commit()

    r = await client.get("/finance/category", params={"name": "Без категории", "month": 12, "year": 2032})
    assert r.status_code == 200
    assert "Последний день" in r.text


@pytest.mark.asyncio
async def test_finance_popup_rejects_bad_month(client, db):
    """month вне 1..12 — это 422, а не 500 с traceback."""
    r = await client.get("/finance/category", params={"name": "Еда", "month": 13, "year": 2032})
    assert r.status_code == 422
    assert "Traceback" not in r.text


@pytest.mark.asyncio
async def test_finance_popup_plural(client, db):
    """Склонение в шапке: 1 операция, 2 операции, 5 операций."""
    db.add_all([
        Transaction(date=date(2033, 1, 2), amount=-100.0, description="Раз", category_id=None),
        Transaction(date=date(2033, 2, 2), amount=-100.0, description="Два", category_id=None),
        Transaction(date=date(2033, 2, 3), amount=-100.0, description="Три", category_id=None),
        Transaction(date=date(2033, 2, 4), amount=-100.0, description="Четыре", category_id=None),
        Transaction(date=date(2033, 2, 5), amount=-100.0, description="Пять", category_id=None),
        Transaction(date=date(2033, 2, 6), amount=-100.0, description="Шесть", category_id=None),
    ])
    await db.commit()

    one = await client.get("/finance/category", params={"name": "Без категории", "month": 1, "year": 2033})
    assert "1 операция" in one.text
    many = await client.get("/finance/category", params={"name": "Без категории", "month": 2, "year": 2033})
    assert "5 операций" in many.text
