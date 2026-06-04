from datetime import date, datetime, timedelta

import pytest

from app.models.task import Task
from app.web.deps import get_today_progress


@pytest.mark.asyncio
async def test_progress_leaf_task(db):
    today = date.today()
    t = Task(title="Позвонить", due_date=today, status="новая", source="web")
    db.add(t)
    await db.commit()

    completed, total = await get_today_progress(db)
    assert total == 1
    assert completed == 0


@pytest.mark.asyncio
async def test_progress_subtasks_weighted(db):
    today = date.today()
    parent = Task(title="Лендинг", due_date=today, status="новая", source="web")
    db.add(parent)
    await db.flush()

    for title in ("A", "B", "C"):
        db.add(Task(title=title, parent_task_id=parent.id, status="новая", source="web"))
    await db.commit()

    completed, total = await get_today_progress(db)
    assert total == 3
    assert completed == 0


@pytest.mark.asyncio
async def test_progress_subtask_completed_today(db):
    today = date.today()
    parent = Task(title="Лендинг", due_date=today, status="новая", source="web")
    db.add(parent)
    await db.flush()

    sub = Task(
        title="Шаг 1",
        parent_task_id=parent.id,
        status="выполнена",
        completed_at=datetime.utcnow(),
        source="web",
        is_archived=False,
    )
    db.add(sub)
    db.add(Task(title="Шаг 2", parent_task_id=parent.id, status="новая", source="web"))
    await db.commit()

    completed, total = await get_today_progress(db)
    assert total == 2
    assert completed == 1


@pytest.mark.asyncio
async def test_progress_subtask_completed_yesterday_not_counted(db):
    today = date.today()
    parent = Task(title="Лендинг", due_date=today, status="новая", source="web")
    db.add(parent)
    await db.flush()

    yesterday = datetime.utcnow() - timedelta(days=1)
    db.add(Task(
        title="Вчера",
        parent_task_id=parent.id,
        status="выполнена",
        completed_at=yesterday,
        source="web",
        is_archived=False,
    ))
    db.add(Task(title="Сегодня", parent_task_id=parent.id, status="новая", source="web"))
    await db.commit()

    completed, total = await get_today_progress(db)
    assert total == 2
    assert completed == 0
