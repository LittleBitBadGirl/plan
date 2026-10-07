"""Пути, где выбор «Сегодня / В бэклог» терялся: оффлайн, внуки, регулярная.

Критик второго прохода нашёл, что тот же дефект, что был в веб-форме, жил ещё на
трёх путях. Эти тесты держат их закрытыми: создание с телефона (оффлайн-очередь),
взятие в день с телефона, снос дерева подзадач и перевод задачи в регулярную.
"""

from datetime import date

import pytest
from sqlalchemy import delete, select

from app.db.database import async_session
from app.models.category import Category
from app.models.impact import CareerImpact
from app.models.recurring import RecurringTask
from app.models.screenshot import Screenshot
from app.models.task import Task


async def _fresh_title(title):
    async with async_session() as s:
        return (
            await s.execute(select(Task).where(Task.title == title))
        ).scalar_one_or_none()


async def _sync(client, actions):
    resp = await client.post("/api/offline/sync", json={"actions": actions})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_offline_create_with_today_puts_task_in_day(client):
    """«Сегодня» с телефона: задача попадает в день, а не ждёт в бэклоге."""
    data = await _sync(
        client,
        [{"client_uuid": "off-today-1", "kind": "create_task",
          "payload": {"title": "Оффлайн в день", "where": "today"}}],
    )
    assert data["results"][0]["status"] == "applied", data

    task = await _fresh_title("Оффлайн в день")
    assert task.planned_for == date.today()


@pytest.mark.asyncio
async def test_offline_create_without_choice_stays_in_backlog(client):
    """Без выбора задача не занимает слот дня: её видно в бэклоге."""
    data = await _sync(
        client,
        [{"client_uuid": "off-backlog-1", "kind": "create_task",
          "payload": {"title": "Оффлайн в бэклог"}}],
    )
    assert data["results"][0]["status"] == "applied", data

    task = await _fresh_title("Оффлайн в бэклог")
    assert task.planned_for is None
    html = (await client.get("/backlog")).text
    assert "Оффлайн в бэклог" in html


@pytest.mark.asyncio
async def test_offline_plan_today_takes_task_into_day(client, db):
    """«Взять на сегодня» с телефона — задача появляется в дне, счётчик переносов жив."""
    task = Task(title="Взять с телефона", status="новая", source="web", item_kind="task")
    db.add(task)
    await db.commit()
    task_id = task.id

    data = await _sync(
        client,
        [{"client_uuid": "off-plan-1", "kind": "plan", "task_id": task_id,
          "payload": {"due_date": date.today().isoformat(), "where": "today"}}],
    )
    assert data["results"][0]["status"] == "applied", data

    async with async_session() as s:
        row = (await s.execute(select(Task).where(Task.id == task_id))).scalar_one()
    assert row.planned_for == date.today()


@pytest.mark.asyncio
async def test_grandchild_subtask_goes_with_root(client, db):
    """Снос уносит всё поддерево: у подзадачи тоже может быть подзадача."""
    root = Task(title="Корень", status="новая", source="web", item_kind="task")
    db.add(root)
    await db.flush()
    child = Task(title="Дитя", parent_task_id=root.id, status="новая", source="web",
                 item_kind="task")
    db.add(child)
    await db.flush()
    db.add(Task(title="Внук", parent_task_id=child.id, status="новая", source="web",
                item_kind="task"))
    await db.commit()
    root_id = root.id

    resp = await client.post(f"/backlog/{root_id}/delete-task")
    assert resp.status_code == 200

    async with async_session() as s:
        left = (await s.execute(select(Task).where(Task.title.in_(["Корень", "Дитя", "Внук"])))).scalars().all()
    assert left == [], "часть поддерева осталась в базе сиротой"


@pytest.mark.asyncio
async def test_make_recurring_does_not_leave_orphan_subtasks(client, db):
    """Перевод в регулярную: задача уходит, подзадачи не остаются без родителя."""
    root = Task(title="Станет регулярной", status="новая", source="web", item_kind="task")
    db.add(root)
    await db.flush()
    db.add(Task(title="Подзадача регулярной", parent_task_id=root.id, status="новая",
                source="web", item_kind="task"))
    await db.commit()
    root_id = root.id

    resp = await client.post(
        f"/backlog/{root_id}/make-recurring",
        data={"recurrence_type": "weekly", "recurrence_days": ["mon"]},
    )
    assert resp.status_code == 200

    async with async_session() as s:
        assert (await s.execute(select(Task).where(Task.title == "Станет регулярной"))).scalar_one_or_none() is None
        orphan = (await s.execute(select(Task).where(Task.title == "Подзадача регулярной"))).scalar_one_or_none()
        assert orphan is None, "подзадача осталась сиротой"
        made = (await s.execute(select(RecurringTask).where(RecurringTask.title == "Станет регулярной"))).scalars().all()
        assert len(made) == 1

        # за собой: шаблон не чистится тестовой фикстурой
        await s.execute(delete(RecurringTask).where(RecurringTask.title == "Станет регулярной"))
        await s.commit()


class _Saturday(date):
    @classmethod
    def today(cls):
        return date(2026, 9, 19)


@pytest.mark.asyncio
async def test_weekend_shows_personal_backlog_tail(client, db, monkeypatch):
    """В субботу ритуала нет: видно личный хвост бэклога, рабочий — скрыт."""
    from app.web.routes import dashboard as dashboard_module

    work_parent = Category(name="Работа", type="task", is_global=1)
    personal = Category(name="Личное", type="task", is_global=1)
    db.add_all([work_parent, personal])
    await db.flush()
    db.add_all(
        [
            Task(title="Личный хвост бэклога", category_id=personal.id, status="новая",
                 source="web", item_kind="task"),
            Task(title="Рабочий хвост бэклога", category_id=work_parent.id, status="новая",
                 source="web", item_kind="task"),
        ]
    )
    await db.commit()

    monkeypatch.setattr(dashboard_module, "date", _Saturday)
    html = (await client.get("/")).text

    assert "Личный хвост бэклога" in html, "в выходной личный хвост должен быть виден"
    assert "Рабочий хвост бэклога" not in html, "рабочее в выходной скрыто"
