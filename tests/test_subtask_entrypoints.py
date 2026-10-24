"""Вход в подзадачи: с карточки дня и из бэклога.

Вера 24.10.2026: «в плане дня в планере невозможно добавить подзадачи, в
бэклоге тоже не понятно как, а вот добавлять последующие логика работает
нормально». Серверная ручка при этом была цела (POST /tasks/{id}/subtasks
отвечал 200), ломался именно вход: на карточке дня это была иконка с одиноким
«плюсиком», а в бэклоге блок подзадач вообще не рисовался у задачи, где
подзадач ещё нет. Поэтому проверяем видимый вход на обеих страницах, а не
только ответ ручки.
"""

from datetime import date

import pytest

from app.models.task import Task


async def _make_task(db, title, planned_for=None):
    task = Task(title=title, source="web", status="новая", planned_for=planned_for)
    db.add(task)
    await db.flush()
    return task


@pytest.mark.asyncio
async def test_day_card_shows_labeled_subtask_entry(client, db):
    """Карточка дня: блок подзадач есть, вход подписан словом, форма на месте."""
    task = await _make_task(db, "Задача дня без подзадач", planned_for=date.today())
    await db.commit()

    html = (await client.get("/")).text

    assert "Задача дня без подзадач" in html, "задача не попала в день"
    assert f'id="subtasks-{task.id}"' in html, "на карточке дня нет блока подзадач"
    assert f'hx-post="/tasks/{task.id}/subtasks"' in html, "нет формы добавления подзадачи"
    assert "Подзадачи" in html, "вход в подзадачи не подписан словом"


@pytest.mark.asyncio
async def test_backlog_has_subtask_entry_for_task_without_subtasks(client, db):
    """Бэклог: у задачи без подзадач тоже есть куда добавить первую."""
    task = await _make_task(db, "Задача бэклога без подзадач")
    await db.commit()

    html = (await client.get("/backlog")).text

    assert "+ подзадача" in html, "нет строки-входа в подзадачи"
    assert f'id="subtasks-{task.id}"' in html, "нет блока подзадач у задачи без подзадач"
    assert f'hx-post="/tasks/{task.id}/subtasks"' in html, "нет формы добавления подзадачи"


@pytest.mark.asyncio
async def test_backlog_subtask_summary_shows_count(client, db):
    """У задачи с подзадачами строка подписана счётчиком, а не «+ подзадача»."""
    parent = await _make_task(db, "Родитель с подзадачами")
    db.add(Task(title="Шаг один", parent_task_id=parent.id, source="web", status="новая"))
    await db.commit()

    html = (await client.get("/backlog")).text

    assert "подзадачи: 0 из 1" in html
    assert f'id="subtasks-{parent.id}"' in html


@pytest.mark.asyncio
async def test_first_subtask_keeps_add_form(client, db):
    """После первой подзадачи форма остаётся — «следующие» добавляются так же."""
    task = await _make_task(db, "Задача дня для первой подзадачи", planned_for=date.today())
    await db.commit()

    resp = await client.post(f"/tasks/{task.id}/subtasks", data={"title": "Первый шаг"})

    assert resp.status_code == 200
    assert "Первый шаг" in resp.text
    assert "subtask-add-input" in resp.text, "форма добавления пропала после первой подзадачи"
