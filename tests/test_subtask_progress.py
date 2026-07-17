from datetime import date, datetime, timedelta

import pytest

from app.models.recurring import RecurringTask
from app.models.recurring_completion import RecurringCompletion
from app.models.task import Task
from app.web.deps import get_today_progress, get_today_actionable_stats, get_subtask_today_progress


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
async def test_progress_parent_with_subs_excluded_from_progress_bar(db):
    """Родитель с подзадачами не входит в полоску ПРОГРЕСС — только в ПОДЗАДАЧИ."""
    today = date.today()
    parent = Task(title="Лендинг", due_date=today, status="новая", source="web")
    db.add(parent)
    await db.flush()

    for i in range(13):
        db.add(Task(title=f"Шаг {i}", parent_task_id=parent.id, status="новая", source="web"))
    await db.commit()

    completed, total = await get_today_progress(db)
    assert total == 0
    assert completed == 0

    sp = await get_subtask_today_progress(db)
    assert sp["parent_total"] == 1
    assert sp["subtask_total"] == 13


@pytest.mark.asyncio
async def test_actionable_subs_without_dl_count(db):
    """Баннер: подзадачи без DL тоже входят в нагрузку."""
    today = date.today()
    parent = Task(title="Лендинг", due_date=today, status="новая", source="web")
    db.add(parent)
    await db.flush()

    for i in range(5):
        db.add(Task(title=f"Шаг {i}", parent_task_id=parent.id, status="новая", source="web"))
    await db.commit()

    completed, total = await get_today_actionable_stats(db)
    assert total == 5
    assert completed == 0


@pytest.mark.asyncio
async def test_actionable_sub_without_dl_completed_today(db):
    """Закрытие подзадачи без DL сегодня увеличивает и total, и completed."""
    today = date.today()
    parent = Task(title="Проект", due_date=today, status="новая", source="web")
    db.add(parent)
    await db.flush()

    db.add(Task(
        title="Без DL",
        parent_task_id=parent.id,
        status="выполнена",
        completed_at=datetime.utcnow(),
        source="web",
    ))
    db.add(Task(title="Ещё одна", parent_task_id=parent.id, status="новая", source="web"))
    await db.commit()

    completed, total = await get_today_actionable_stats(db)
    assert total == 2
    assert completed == 1


@pytest.mark.asyncio
async def test_actionable_subs_with_deadline_today(db):
    """Баннер: подзадачи с deadline <= сегодня считаются поштучно."""
    today = date.today()
    parent = Task(title="Лендинг", due_date=today, status="новая", source="web")
    db.add(parent)
    await db.flush()

    db.add(Task(title="Шаг 1", parent_task_id=parent.id, deadline=today, status="новая", source="web"))
    db.add(Task(
        title="Шаг 2",
        parent_task_id=parent.id,
        deadline=today,
        status="выполнена",
        completed_at=datetime.utcnow(),
        source="web",
    ))
    db.add(Task(title="Шаг 3", parent_task_id=parent.id, deadline=today, status="новая", source="web"))
    await db.commit()

    completed, total = await get_today_actionable_stats(db)
    assert total == 3
    assert completed == 1


@pytest.mark.asyncio
async def test_actionable_subs_future_deadline_not_counted(db):
    """Подзадачи с deadline в будущем не портят статистику."""
    today = date.today()
    future = today + timedelta(days=7)
    parent = Task(title="Проект", due_date=today, status="новая", source="web")
    db.add(parent)
    await db.flush()

    db.add(Task(title="Сегодня", parent_task_id=parent.id, deadline=today, status="новая", source="web"))
    db.add(Task(title="Потом", parent_task_id=parent.id, deadline=future, status="новая", source="web"))
    await db.commit()

    completed, total = await get_today_actionable_stats(db)
    assert total == 1
    assert completed == 0


@pytest.mark.asyncio
async def test_actionable_overdue_sub_counted(db):
    """Просроченные подзадачи (deadline <= сегодня) входят в нагрузку."""
    today = date.today()
    yesterday = today - timedelta(days=1)
    parent = Task(title="Проект", due_date=today, status="новая", source="web")
    db.add(parent)
    await db.flush()

    db.add(Task(title="Вчера", parent_task_id=parent.id, deadline=yesterday, status="новая", source="web"))
    await db.commit()

    completed, total = await get_today_actionable_stats(db)
    assert total == 1
    assert completed == 0


@pytest.mark.asyncio
async def test_actionable_sub_completed_yesterday_not_in_today(db):
    """Подзадача, закрытая вчера, не входит в сегодняшнюю нагрузку."""
    today = date.today()
    parent = Task(title="Лендинг", due_date=today, status="новая", source="web")
    db.add(parent)
    await db.flush()

    yesterday = datetime.utcnow() - timedelta(days=1)
    db.add(Task(
        title="Вчера",
        parent_task_id=parent.id,
        deadline=today - timedelta(days=1),
        status="выполнена",
        completed_at=yesterday,
        source="web",
    ))
    db.add(Task(title="Сегодня", parent_task_id=parent.id, deadline=today, status="новая", source="web"))
    await db.commit()

    completed, total = await get_today_actionable_stats(db)
    assert total == 1
    assert completed == 0


@pytest.mark.asyncio
async def test_actionable_mixed_leaf_and_subs(db):
    """Standalone + подзадачи с deadline."""
    today = date.today()
    open_leaf = Task(title="Письмо", due_date=today, status="новая", source="web")
    parent = Task(title="Проект", due_date=today, status="новая", source="web")
    db.add_all([open_leaf, parent])
    await db.flush()

    db.add(Task(
        title="Sub1",
        parent_task_id=parent.id,
        deadline=today,
        status="выполнена",
        completed_at=datetime.utcnow(),
        source="web",
    ))
    db.add(Task(title="Sub2", parent_task_id=parent.id, deadline=today, status="новая", source="web"))
    await db.commit()

    completed, total = await get_today_actionable_stats(db)
    assert total == 3  # 1 leaf + 2 subs with deadline
    assert completed == 1


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
async def test_progress_includes_recurring_templates(db):
    """Регулярные шаблоны на сегодня входят в total."""
    today = date.today()
    db.add(Task(title="Обычная", due_date=today, status="новая", source="web"))
    db.add(
        RecurringTask(
            title="Йога",
            recurrence_type="daily",
            start_date=today,
            is_active=True,
        )
    )
    await db.commit()

    completed, total = await get_today_progress(db)
    assert total == 2
    assert completed == 0


@pytest.mark.asyncio
async def test_progress_recurring_completed_today(db):
    """Выполненная регулярная задача увеличивает completed."""
    today = date.today()
    rt = RecurringTask(
        title="Вечерний уход",
        recurrence_type="daily",
        start_date=today,
        is_active=True,
    )
    db.add(rt)
    await db.flush()
    db.add(
        RecurringCompletion(
            recurring_task_id=rt.id,
            occurrence_date=today,
            status="completed",
        )
    )
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
