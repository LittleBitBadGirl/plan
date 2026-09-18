"""Счётчик просрочки: бейдж «сколько задача тянется».

Раньше бейдж брался из `postpones`, который сбрасывался при ручном переносе
задачи на будущую дату и считался только по рабочим дням. Из-за этого у задачи,
висящей больше недели, показывалось «×2». Теперь значение считается от дня,
когда задачу должны были сделать (`overdue_since`), и не зависит ни от ручных
переносов, ни от того, отработал ли ночной перенос.
"""
from datetime import date, timedelta

import pytest

from app.models.task import Task
from app.services.postpones_service import apply_manual_plan, apply_rollover


def _task(due_date, overdue_since=None, postpones=0):
    task = Task(title="проверка")
    task.due_date = due_date
    task.overdue_since = overdue_since
    task.postpones = postpones
    return task


def test_badge_counts_days_since_task_went_overdue():
    task = _task(due_date=date.today(), overdue_since=date.today() - timedelta(days=8))

    assert task.overdue_days == 8


def test_badge_is_zero_for_task_due_today():
    assert _task(due_date=date.today()).overdue_days == 0


def test_badge_falls_back_to_due_date_when_anchor_missing():
    """Якорь мог не проставиться (старые задачи) — считаем от самой даты."""
    task = _task(due_date=date.today() - timedelta(days=5))

    assert task.overdue_days == 5


def test_rollover_sets_anchor_once_and_keeps_it():
    """Первый перенос запоминает день, второй — не сдвигает якорь."""
    task = _task(due_date=date.today() - timedelta(days=3))
    apply_rollover(task, date.today())

    assert task.overdue_since == date.today() - timedelta(days=3)
    assert task.due_date == date.today()

    # Задача уже «уехала» на сегодня: следующая ночь якорь не двигает.
    apply_rollover(task, date.today())
    assert task.overdue_since == date.today() - timedelta(days=3)


def test_manual_move_does_not_reset_badge():
    """Раньше перенос руками обнулял счётчик — из-за этого бейдж и врал.

    apply_manual_plan правит только postpones (саму дату ставит вызывающий код),
    поэтому проверяем именно то, что важно: сброс postpones не трогает якорь.
    """
    anchor = date.today() - timedelta(days=9)
    task = _task(due_date=date.today(), overdue_since=anchor, postpones=9)
    apply_manual_plan(task, date.today(), date.today() + timedelta(days=7))

    assert task.postpones == 0
    assert task.overdue_since == anchor
    assert task.overdue_days == 9


def test_badge_survives_missed_rollovers():
    """Даже если ночной перенос не отработал, число считается от якоря."""
    task = _task(due_date=date.today() - timedelta(days=1), overdue_since=date.today() - timedelta(days=1))

    assert task.overdue_days == 1


@pytest.mark.asyncio
async def test_completed_task_stops_counting(client, db):
    """Закрытая задача больше не «тянется»."""
    from sqlalchemy import select

    from app.db.database import async_session

    async with async_session() as session:
        task = Task(
            title="Закрыть и проверить",
            due_date=date.today(),
            overdue_since=date.today() - timedelta(days=4),
            status="новая",
            item_kind="task",
            source="web",
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    resp = await client.post(f"/tasks/{task_id}/complete")
    assert resp.status_code == 200

    async with async_session() as session:
        stored = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert stored.overdue_since is None
        assert stored.overdue_days == 0
