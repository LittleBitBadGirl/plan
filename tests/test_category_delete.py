"""Удаление категории не должно оставлять потеряшек.

Контракт: использованная категория без цели не удаляется (409 со списком
кандидатов), с целью — записи переезжают, а категория исчезает. За категорией
могут держаться транзакции, задачи, привычки, регулярные задачи, цели,
записи чтения и подкатегории — все проверяются и переносятся.
"""

from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import delete as sa_delete, select

from app.models.category import Category
from app.models.finance import Transaction
from app.models.goal import FinancialGoal
from app.models.habit import Habit
from app.models.recurring import RecurringTask
from app.models.task import Task
from app.services.category_delete_service import delete_category_with_move

# Что этот файл создаёт — по этим маркерам и убираем за собой: база в тестах
# общая на сессию, и забытые привычки с целями ломают чужие счётчики.
_TX_DESCRIPTIONS = (
    "В подкате",
    "Транзакция",
    "Останется без категории",
    "В корне",
    "Через safe",
)
_TASK_TITLES = ("Задача в категории", "Задача без прочего")
_HABIT_TITLES = ("Привычка в категории", "Единственная привычка")
_RECURRING_TITLES = ("Регулярная в категории",)
_GOAL_NAMES = ("Цель в категории",)


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_created_rows(db):
    yield
    from app.models.shopping import ShoppingItem

    await db.execute(sa_delete(Transaction).where(Transaction.description.in_(_TX_DESCRIPTIONS)))
    await db.execute(sa_delete(Task).where(Task.title.in_(_TASK_TITLES)))
    await db.execute(sa_delete(Habit).where(Habit.title.in_(_HABIT_TITLES)))
    await db.execute(sa_delete(RecurringTask).where(RecurringTask.title.in_(_RECURRING_TITLES)))
    await db.execute(sa_delete(FinancialGoal).where(FinancialGoal.name.in_(_GOAL_NAMES)))
    await db.execute(sa_delete(ShoppingItem).where(ShoppingItem.title == "Книга в категории"))
    await db.execute(sa_delete(Category).where(Category.id >= 9200))
    await db.commit()


async def _make_finance_pair(db, base=9201):
    """Пара «родитель + подкатегория» и отдельная цель переноса.

    id задаём явно и разными блоками на тест: база в тестах общая на сессию,
    и повторный INSERT тех же id падает по UNIQUE.
    """
    parent = Category(id=base, name=f"Удаление Родитель {base}", type="finance", is_global=True)
    db.add(parent)
    sub = Category(id=base + 1, name=f"Удаление Подкат {base}", type="finance", parent_id=base, is_global=False)
    db.add(sub)
    other = Category(id=base + 2, name=f"Удаление Цель {base}", type="finance", is_global=True)
    db.add(other)
    await db.commit()
    return parent, sub, other


@pytest.mark.asyncio
async def test_delete_used_category_asks_where_to_move(client, db):
    """Без move_to использованная категория не удаляется: 409 + кандидаты."""
    parent, sub, other = await _make_finance_pair(db, 9201)
    db.add(Transaction(date=date(2031, 5, 5), amount=-100.0, description="В подкате", category_id=sub.id))
    await db.commit()

    r = await client.delete(f"/api/categories/{sub.id}")
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "category_in_use"
    assert body["usage"]["transactions"] == 1
    assert body["allow_null"] is True
    assert body["default_move_to"] == parent.id
    ids = [c["id"] for c in body["candidates"]]
    assert other.id in ids and sub.id not in ids

    # ничего не удалено и не переехало
    assert (await db.execute(select(Category).where(Category.id == sub.id))).scalar_one_or_none() is not None
    tx = (await db.execute(select(Transaction).where(Transaction.category_id == sub.id))).scalars().all()
    assert len(tx) == 1


@pytest.mark.asyncio
async def test_delete_with_move_to_moves_records(client, db):
    """С move_to переезжают транзакции, задачи, привычки, регулярки и цели."""
    parent, sub, other = await _make_finance_pair(db, 9211)
    db.add_all([
        Transaction(date=date(2031, 5, 6), amount=-200.0, description="Транзакция", category_id=sub.id),
        Task(title="Задача в категории", category_id=sub.id, status="todo", is_archived=False),
        Habit(title="Привычка в категории", category_id=sub.id),
        RecurringTask(
            title="Регулярная в категории",
            category_id=sub.id,
            recurrence_type="daily",
            start_date=date(2031, 5, 1),
        ),
        FinancialGoal(name="Цель в категории", target_amount=1000, category_id=sub.id),
    ])
    await db.commit()

    r = await client.delete(f"/api/categories/{sub.id}?move_to={other.id}")
    assert r.status_code == 200

    assert (await db.execute(select(Category).where(Category.id == sub.id))).scalar_one_or_none() is None
    for model, title_field, title in (
        (Transaction, "description", "Транзакция"),
        (Task, "title", "Задача в категории"),
        (Habit, "title", "Привычка в категории"),
        (RecurringTask, "title", "Регулярная в категории"),
        (FinancialGoal, "name", "Цель в категории"),
    ):
        row = (await db.execute(select(model).where(getattr(model, title_field) == title))).scalar_one()
        assert row.category_id == other.id, f"{model.__name__} не переехал"


@pytest.mark.asyncio
async def test_habit_alone_blocks_deletion(client, db):
    """Одна привычка (без транзакций) уже повод спросить: раньше удалялось молча."""
    db.add(Category(id=9215, name="Удаление Привычки", type="task", is_global=True))
    await db.commit()
    db.add(Habit(title="Единственная привычка", category_id=9215))
    await db.commit()

    r = await client.delete("/api/categories/9215")
    assert r.status_code == 409
    assert r.json()["usage"]["habits"] == 1


@pytest.mark.asyncio
async def test_delete_empty_category_needs_no_target(client, db):
    """Пустая категория удаляется сразу, без вопросов."""
    db.add(Category(id=9210, name="Удаление Пустая", type="finance", is_global=True))
    await db.commit()

    r = await client.delete("/api/categories/9210")
    assert r.status_code == 200
    assert (await db.execute(select(Category).where(Category.id == 9210))).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_children_move_to_chosen_target(client, db):
    """Подкатегории переезжают туда, что выбрали в диалоге."""
    db.add(Category(id=9230, name="Удаление Корень Детей", type="finance", is_global=True))
    db.add(Category(id=9231, name="Удаление Дитя", type="finance", parent_id=9230, is_global=False))
    db.add(Category(id=9232, name="Удаление Новая Цель", type="finance", is_global=True))
    await db.commit()

    r = await client.delete("/api/categories/9230?move_to=9232")
    assert r.status_code == 200
    child = (await db.execute(select(Category).where(Category.id == 9231))).scalar_one()
    assert child.parent_id == 9232


@pytest.mark.asyncio
async def test_delete_to_null_keeps_records(client, db):
    """move_to=0 — «без категории»: записи остаются, но с NULL."""
    db.add(Category(id=9240, name="Удаление В Никуда", type="finance", is_global=True))
    await db.commit()
    db.add(Transaction(date=date(2031, 5, 9), amount=-500.0, description="Останется без категории", category_id=9240))
    await db.commit()

    r = await client.delete("/api/categories/9240?move_to=0")
    assert r.status_code == 200
    tx = (await db.execute(select(Transaction).where(Transaction.description == "Останется без категории"))).scalar_one()
    assert tx.category_id is None


@pytest.mark.asyncio
async def test_delete_rejects_move_to_child(db):
    """Перенос в собственного потомка дал бы петлю — отказ с причиной."""
    parent = Category(id=9220, name="Удаление Корень", type="finance", is_global=True)
    db.add(parent)
    child = Category(id=9221, name="Удаление Ребёнок", type="finance", parent_id=9220, is_global=False)
    db.add(child)
    await db.commit()
    db.add(Transaction(date=date(2031, 5, 7), amount=-300.0, description="В корне", category_id=parent.id))
    await db.commit()

    payload = await delete_category_with_move(db, parent, 9221)
    assert payload is not None and payload["error"] == "invalid_target"
    assert (await db.execute(select(Category).where(Category.id == 9220))).scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_reading_category_frees_items(client, db):
    """Категория чтения не спрашивает: записи уезжают на полку «без категории»."""
    from app.models.shopping import ShoppingItem

    db.add(Category(id=9250, name="Удаление Чтение", type="reading", is_global=True))
    await db.commit()
    db.add(ShoppingItem(title="Книга в категории", category_id=9250))
    await db.commit()

    r = await client.delete("/api/categories/9250")
    assert r.status_code == 200
    item = (await db.execute(select(ShoppingItem).where(ShoppingItem.title == "Книга в категории"))).scalar_one()
    assert item.category_id is None


@pytest.mark.asyncio
async def test_safe_endpoint_still_moves_to_parent(client, db):
    """Старый /safe работает как раньше: подкатегория → записи к родителю."""
    parent, sub, other = await _make_finance_pair(db, 9241)
    db.add(Transaction(date=date(2031, 5, 8), amount=-400.0, description="Через safe", category_id=sub.id))
    await db.commit()

    r = await client.delete(f"/api/categories/{sub.id}/safe")
    assert r.status_code == 200
    tx = (await db.execute(select(Transaction).where(Transaction.description == "Через safe"))).scalars().all()
    assert tx[0].category_id == parent.id


@pytest.mark.asyncio
async def test_safe_without_prochee_falls_back_to_null(client, db):
    """Нет ни родителя, ни «Прочее» — /safe уводит записи в NULL, а не в 409."""
    db.add(Category(id=9260, name="Удаление Без Прочего", type="task", is_global=True))
    await db.commit()
    db.add(Task(title="Задача без прочего", category_id=9260, status="todo", is_archived=False))
    await db.commit()

    r = await client.delete("/api/categories/9260/safe")
    assert r.status_code == 200
    task = (await db.execute(select(Task).where(Task.title == "Задача без прочего"))).scalar_one()
    assert task.category_id is None
