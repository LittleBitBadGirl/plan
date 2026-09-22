"""Закрытые задачи не «мелькают» в списках: их место — Архив.

Закрытая подзадача остаётся у родителя (по ней считается прогресс «N/M»),
поэтому в архиве она не нужна, но и в календаре и в списке всех задач её быть
не должно: иначе дни и списки выглядят свалкой старых закрытых задач.
"""

from datetime import date, datetime

import pytest

from app.models.task import Task


async def _make(db, **kw):
    task = Task(**kw)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


@pytest.mark.asyncio
async def test_calendar_day_hides_closed_tasks(client, db):
    today = date.today()
    parent = await _make(db, title="Родитель", due_date=today, status="новая", source="web")
    closed = await _make(
        db,
        title="Закрытая подзадача",
        due_date=today,
        status="выполнена",
        completed_at=datetime.utcnow(),
        is_archived=False,
        parent_task_id=parent.id,
        source="web",
    )

    resp = await client.get(f"/api/tasks/date/{today.isoformat()}")
    assert resp.status_code == 200
    titles = [t["title"] for t in resp.json()]
    assert "Родитель" in titles
    assert "Закрытая подзадача" not in titles
    assert closed.status == "выполнена"  # в базе задача не тронута


@pytest.mark.asyncio
async def test_tasks_page_hides_closed_but_shows_with_explicit_filter(client, db):
    today = date.today()
    await _make(db, title="Открытая задача", due_date=today, status="новая", source="web")
    await _make(
        db,
        title="Закрытая задача",
        due_date=today,
        status="выполнена",
        completed_at=datetime.utcnow(),
        is_archived=False,
        source="web",
    )

    html = (await client.get("/tasks")).text
    assert "Открытая задача" in html
    assert "Закрытая задача" not in html

    # Явный фильтр по статусу остаётся рабочим
    only_closed = (await client.get("/tasks?status=выполнена")).text
    assert "Закрытая задача" in only_closed
