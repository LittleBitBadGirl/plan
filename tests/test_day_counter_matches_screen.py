"""Счётчик дня обязан совпадать с тем, что видно на экране.

Вера 28.10.2026: «когда я с дашборда убираю обратно в бэклог задачи, счётчик не
уменьшается: убрала 1 и теперь некорректное отображение — осталось 2 задачи, а
на счётчике надо ещё 3 закрыть».

Причин было две, и обе проверяются здесь:

1. Задача со статусом, которого нет в словаре приложения (`open` — так его
   записала интеграция Hermes мимо ORM), попадала в счётчики «на день» и
   «прогресс», но не проходила фильтр списка дня: на экране её не было, а
   счётчик её считал.
2. Взятые на день подзадачи не входили в полоску «Прогресс» вовсе. Вера берёт в
   день куски больших задач, поэтому возврат подзадачи убирал карточку, но не
   число: «на день» падало, а прогресс оставался прежним.
"""
from datetime import date, datetime

import pytest
from sqlalchemy import select

from app.db.database import async_session
from app.models.task import Task, task_is_open
from app.web.deps import build_day_pool_context, get_dashboard_day_stats, get_today_progress


async def _fresh(fn):
    """Прочитать число заново из базы, а не из кеша сессии."""
    async with async_session() as s:
        return await fn(s)


@pytest.mark.asyncio
async def test_foreign_status_is_visible_in_day(client, db):
    """Статус `open` из интеграции не делает задачу невидимой на дашборде."""
    today = date.today()
    db.add(Task(
        title="Психолог: ревность — отдать письмо",
        status="open",
        source="hermes",
        planned_for=today,
        item_kind="task",
    ))
    await db.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Психолог: ревность — отдать письмо" in resp.text, (
        "задача со статусом open не попала в список дня, хотя счётчик её считает"
    )


@pytest.mark.asyncio
async def test_counter_equals_visible_cards(db):
    """Числа сводки считаются по тем же задачам, что видит список дня."""
    today = date.today()
    db.add(Task(title="Открытая с чужим статусом", status="open", source="hermes",
                planned_for=today, item_kind="task"))
    db.add(Task(title="Открытая обычная", status="новая", source="web",
                planned_for=today, item_kind="task"))
    await db.commit()

    bundle = await _fresh(get_dashboard_day_stats)
    open_rows = await _fresh(
        lambda s: s.execute(
            select(Task).where(
                Task.planned_for == today,
                task_is_open(),
                Task.parent_task_id.is_(None),
                Task.item_kind == "task",
            )
        )
    )
    open_titles = [t.title for t in open_rows.scalars().all()]

    assert bundle.total == 2, "обе открытые задачи дня обязаны быть в знаменателе"
    assert bundle.completed == 0
    assert len(open_titles) == bundle.total - bundle.completed, (
        "«закрыть ещё N» не совпадает с числом открытых карточек дня"
    )


@pytest.mark.asyncio
async def test_returning_subtask_decrements_day_progress(client, db):
    """Возврат взятой подзадачи в бэклог уменьшает прогресс на одну карточку."""
    today = date.today()
    done_root = Task(
        title="Закрытая корневая",
        planned_for=today,
        status="выполнена",
        completed_at=datetime.utcnow(),
        is_archived=True,
        source="web",
        item_kind="task",
    )
    parent = Task(title="Большая задача", status="новая", source="web", item_kind="task")
    db.add_all([done_root, parent])
    await db.flush()
    sub1 = Task(title="Кусок 1", parent_task_id=parent.id, status="новая",
                source="web", item_kind="task", planned_for=today)
    sub2 = Task(title="Кусок 2", parent_task_id=parent.id, status="новая",
                source="web", item_kind="task", planned_for=today)
    db.add_all([sub1, sub2])
    await db.commit()
    sub_id = sub1.id

    assert await _fresh(get_today_progress) == (1, 3)

    resp = await client.post(f"/tasks/{sub_id}/backlog")
    assert resp.status_code == 200

    assert await _fresh(get_today_progress) == (1, 2), (
        "после возврата подзадачи в бэклог знаменатель прогресса не уменьшился"
    )

    pool = await _fresh(lambda s: build_day_pool_context(s, None, today))
    assert pool["taken"] == 2, "«на день» и «прогресс» считают одно и то же"


@pytest.mark.asyncio
async def test_returning_root_task_decrements_day_progress(client, db):
    """Возврат корневой задачи уносит её из прогресса вместе с карточкой."""
    today = date.today()
    root = Task(title="Корневая на день", planned_for=today, status="новая",
                source="web", item_kind="task")
    db.add(root)
    await db.commit()
    root_id = root.id

    assert await _fresh(get_today_progress) == (0, 1)
    resp = await client.post(f"/tasks/{root_id}/backlog")
    assert resp.status_code == 200
    assert await _fresh(get_today_progress) == (0, 0)


@pytest.mark.asyncio
async def test_api_normalizes_foreign_status(client, db):
    """PUT /api/tasks приводит чужое слово статуса к словарю приложения."""
    task = Task(title="Из интеграции", status="новая", source="hermes", item_kind="task")
    db.add(task)
    await db.commit()
    task_id = task.id

    resp = await client.put(f"/api/tasks/{task_id}", json={"status": "open"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "новая"

    stored = await _fresh(
        lambda s: s.execute(select(Task.status).where(Task.id == task_id))
    )
    assert stored.scalar_one() == "новая"
