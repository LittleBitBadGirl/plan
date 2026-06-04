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
async def test_progress_parent_with_subs_counts_as_one_card(db):
    """13 подзадач = 1 карточка в total, не 13."""
    today = date.today()
    parent = Task(title="Лендинг", due_date=today, status="новая", source="web")
    db.add(parent)
    await db.flush()

    for i in range(13):
        db.add(Task(title=f"Шаг {i}", parent_task_id=parent.id, status="новая", source="web"))
    await db.commit()

    completed, total = await get_today_progress(db)
    assert total == 1
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
    assert total == 1
    assert completed == 1


@pytest.mark.asyncio
async def test_progress_mixed_leaf_and_subtasks(db):
    """X = закрытые листья + чекнутые подзадачи, Y = все карточки."""
    today = date.today()
    done_leaf = Task(
        title="Звонок",
        due_date=today,
        status="выполнена",
        completed_at=datetime.utcnow(),
        is_archived=True,
        source="web",
    )
    open_leaf = Task(title="Письмо", due_date=today, status="новая", source="web")
    parent = Task(title="Проект", due_date=today, status="новая", source="web")
    db.add_all([done_leaf, open_leaf, parent])
    await db.flush()

    db.add(Task(
        title="Sub1",
        parent_task_id=parent.id,
        status="выполнена",
        completed_at=datetime.utcnow(),
        source="web",
    ))
    db.add(Task(title="Sub2", parent_task_id=parent.id, status="новая", source="web"))
    await db.commit()

    completed, total = await get_today_progress(db)
    assert total == 3
    assert completed == 2


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
    assert total == 1
    assert completed == 0


@pytest.mark.asyncio
async def test_progress_archived_leaf_completed_today(db):
    today = date.today()
    done = Task(
        title="Сделано",
        due_date=today,
        status="выполнена",
        completed_at=datetime.utcnow(),
        is_archived=True,
        source="web",
    )
    open_task = Task(title="Осталось", due_date=today, status="новая", source="web")
    db.add(done)
    db.add(open_task)
    await db.commit()

    completed, total = await get_today_progress(db)
    assert total == 2
    assert completed == 1


@pytest.mark.asyncio
async def test_progress_parent_closed_today_counts_as_one(db):
    """Закрытие родителя целиком = +1, не по числу подзадач."""
    today = date.today()
    parent = Task(
        title="Проект",
        due_date=today,
        status="выполнена",
        completed_at=datetime.utcnow(),
        is_archived=True,
        source="web",
    )
    db.add(parent)
    await db.flush()

    now = datetime.utcnow()
    for title in ("A", "B", "C"):
        db.add(Task(
            title=title,
            parent_task_id=parent.id,
            status="выполнена",
            completed_at=now,
            source="web",
            is_archived=False,
        ))
    await db.commit()

    completed, total = await get_today_progress(db)
    assert total == 1
    assert completed == 1


@pytest.mark.asyncio
async def test_archived_subtask_visible_after_repair(db):
    today = date.today()
    parent = Task(title="Проект", due_date=today, status="новая", source="web")
    db.add(parent)
    await db.flush()

    db.add(Task(
        title="Скрытая",
        parent_task_id=parent.id,
        status="выполнена",
        completed_at=datetime.utcnow(),
        is_archived=True,
        source="web",
    ))
    await db.commit()

    from app.web.deps import repair_archived_subtasks, load_subtasks_map
    await repair_archived_subtasks(db)
    await db.commit()

    subtasks_map = await load_subtasks_map(db, [parent.id])
    assert len(subtasks_map[parent.id]) == 1
    assert subtasks_map[parent.id][0].title == "Скрытая"
