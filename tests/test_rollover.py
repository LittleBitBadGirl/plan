import pytest
from datetime import date, timedelta
from app.models.task import Task
from app.services.rollover_service import rollover_overdue_tasks
from app.services.postpones_service import apply_manual_plan


@pytest.mark.asyncio
async def test_rollover_overdue_tasks(db):
    """Тест переноса просроченных задач на 1 день"""
    yesterday = date.today() - timedelta(days=1)

    task = Task(
        title="Просроченная задача",
        status="новая",
        due_date=yesterday,
    )
    db.add(task)
    await db.flush()

    result = await rollover_overdue_tasks(db)

    assert result["moved"] == 1
    assert task.due_date == date.today()
    assert task.postpones == 1


@pytest.mark.asyncio
async def test_rollover_multi_day_overdue(db):
    """Просрочка на несколько дней — считаем все дни, не +1"""
    five_days_ago = date.today() - timedelta(days=5)

    task = Task(
        title="Долго просроченная",
        status="новая",
        due_date=five_days_ago,
        postpones=2,
    )
    db.add(task)
    await db.flush()

    result = await rollover_overdue_tasks(db)

    assert result["moved"] == 1
    assert task.due_date == date.today()
    assert task.postpones == 7


@pytest.mark.asyncio
async def test_manual_plan_from_backlog_resets_postpones(db):
    """Ручное планирование из бэклога на сегодня — сброс счётчика"""
    task = Task(
        title="Из бэклога",
        status="новая",
        due_date=None,
        postpones=4,
    )
    db.add(task)
    await db.flush()

    today = date.today()
    apply_manual_plan(task, None, today)
    task.due_date = today

    assert task.postpones == 0


@pytest.mark.asyncio
async def test_manual_plan_overdue_catch_up(db):
    """Ручной перенос просроченной задачи на сегодня — догоняем пропущенные дни"""
    three_days_ago = date.today() - timedelta(days=3)
    task = Task(
        title="Просроченная вручную",
        status="новая",
        due_date=three_days_ago,
        postpones=1,
    )
    db.add(task)
    await db.flush()

    today = date.today()
    apply_manual_plan(task, three_days_ago, today)
    task.due_date = today

    assert task.postpones == 4
