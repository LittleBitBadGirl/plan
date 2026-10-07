"""Механика дня от 07.10.2026: день набирается руками, вечером возвращается.

Что проверяем: ночная зачистка выкидывает незакрытое из дня в бэклог (а не
тащит всё в сегодня, как прежний ролловер), счётчик переносов растёт и не
сбрасывается при взятии задачи, счётчик дня читается как «взято N из 5».
"""
import pytest
from datetime import date, timedelta

from app.models.task import Task
from app.services.rollover_service import rollover_overdue_tasks
from app.services.day_pool_service import (
    MIN_DAY_TASKS,
    count_backlog,
    count_taken,
    counter_label,
    return_to_backlog,
    take_to_day,
)


@pytest.mark.asyncio
async def test_night_return_sends_unfinished_to_backlog(db):
    """Вчерашняя незакрытая задача уходит в бэклог и получает перенос."""
    yesterday = date.today() - timedelta(days=1)
    task = Task(title="Вчерашняя", status="новая", planned_for=yesterday)
    db.add(task)
    await db.flush()

    result = await rollover_overdue_tasks(db)

    assert result["moved"] == 1
    assert task.planned_for is None
    assert task.postpones == 1
    assert task.overdue_since == yesterday


@pytest.mark.asyncio
async def test_night_return_counts_every_missed_day(db):
    """Три дня висела в дне — три переноса, а не +1."""
    three_days_ago = date.today() - timedelta(days=3)
    task = Task(title="Зависла", status="новая", planned_for=three_days_ago, postpones=2)
    db.add(task)
    await db.flush()

    result = await rollover_overdue_tasks(db)

    assert result["moved"] == 1
    assert task.postpones == 5  # 2 прежних + 3 дня в дне
    assert task.planned_for is None


@pytest.mark.asyncio
async def test_night_return_does_not_touch_todays_taken_task(db):
    """Взятое сегодня остаётся в дне — иначе день обнулялся бы каждую ночь."""
    today = date.today()
    task = Task(title="Взята сегодня", status="новая", planned_for=today)
    db.add(task)
    await db.flush()

    result = await rollover_overdue_tasks(db)

    assert result["moved"] == 0
    assert task.planned_for == today
    assert (task.postpones or 0) == 0


@pytest.mark.asyncio
async def test_night_return_ignores_backlog(db):
    """Задача из бэклога в дне не была — её переносы не трогаем."""
    task = Task(title="Ждёт в бэклоге", status="новая", planned_for=None, postpones=3)
    db.add(task)
    await db.flush()

    result = await rollover_overdue_tasks(db)

    assert result["moved"] == 0
    assert task.planned_for is None
    assert task.postpones == 3


@pytest.mark.asyncio
async def test_closed_task_stays_where_it_is(db):
    """Закрытая задача не возвращается в бэклог и не получает перенос."""
    yesterday = date.today() - timedelta(days=1)
    task = Task(title="Сделана вчера", status="выполнена", planned_for=yesterday)
    db.add(task)
    await db.flush()

    result = await rollover_overdue_tasks(db)

    assert result["moved"] == 0
    assert task.planned_for == yesterday
    assert (task.postpones or 0) == 0


@pytest.mark.asyncio
async def test_take_does_not_reset_postpones(db):
    """Взятие задачи из бэклога не обнуляет счётчик: он про «брал и не доделал»."""
    task = Task(title="Хроническая", status="новая", postpones=6)
    db.add(task)
    await db.flush()

    await take_to_day(db, task)

    assert task.planned_for == date.today()
    assert task.postpones == 6


@pytest.mark.asyncio
async def test_return_to_backlog_removes_from_day(db):
    """Крестик в дне возвращает задачу в бэклог, переносы не трогает."""
    task = Task(title="Передумала", status="новая", planned_for=date.today(), postpones=2)
    db.add(task)
    await db.flush()

    await return_to_backlog(db, task)

    assert task.planned_for is None
    assert task.postpones == 2


@pytest.mark.asyncio
async def test_day_counter_counts_taken_including_closed(db):
    """Счётчик дня считает и закрытые: иначе после пяти задач он падал бы к нулю."""
    today = date.today()
    for i in range(3):
        db.add(Task(title=f"Взята {i}", status="новая", planned_for=today))
    db.add(Task(title="Закрыта", status="выполнена", planned_for=today))
    db.add(Task(title="В бэклоге", status="новая", planned_for=None))
    await db.flush()

    assert await count_taken(db, today) == 4
    assert await count_backlog(db) == 1


def test_counter_label_reads_like_the_ritual():
    """До пяти — «из 5», после пяти знаменатель не нужен (это не лимит)."""
    assert counter_label(0) == "взято 0 из 5"
    assert counter_label(MIN_DAY_TASKS) == "взято 5, минимум набран"
    assert counter_label(6) == "взято 6"
    assert counter_label(9) == "взято 9"
