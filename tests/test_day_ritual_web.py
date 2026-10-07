"""Веб-слой дня и бэклога: то, что критик 07.10.2026 назвал непроверенным.

Проверяем именно поведение через HTTP, а не только сервисы: форма создания и
редактирования реально ставят задачу в день (planned_for), бэклог разведён на
две половины, удаление уносит подзадачи и подчищает ссылки, архив не показывают
подзадачи, а взятую подзадачу видно в дне.
"""

from datetime import date

import pytest
from sqlalchemy import delete, select

from app.db.database import async_session
from app.models.category import Category
from app.models.impact import CareerImpact
from app.models.screenshot import Screenshot
from app.models.task import Task


async def _get(title):
    """Свежая сессия: читать после write из другого соединения."""
    async with async_session() as s:
        return (
            await s.execute(select(Task).where(Task.title == title))
        ).scalar_one_or_none()


async def _make_category(db, name, parent_id=None, is_global=1):
    cat = Category(name=name, type="task", is_global=is_global, parent_id=parent_id)
    db.add(cat)
    await db.flush()
    return cat


@pytest.mark.asyncio
async def test_create_form_today_puts_task_in_day(client):
    """«Сегодня» в форме создания — задача сразу в дне, а не в бэклоге.

    Это и был BLOCKER критика: форма шлёт where, а обработчик его не читал.
    """
    resp = await client.post(
        "/tasks/web/create", data={"title": "Задача через форму в день", "where": "today"}
    )
    assert resp.status_code in (200, 303)

    task = await _get("Задача через форму в день")
    assert task is not None, "задача не создалась"
    assert task.planned_for == date.today(), "выбор «Сегодня» не поставил задачу в день"

    dashboard = await client.get("/")
    assert "Задача через форму в день" in dashboard.text


@pytest.mark.asyncio
async def test_create_form_default_is_backlog(client):
    """Без выбора (или выбор «В бэклог») задача ждёт в бэклоге, а не влазит в день."""
    await client.post(
        "/tasks/web/create", data={"title": "Задача по умолчанию", "where": "backlog"}
    )
    task = await _get("Задача по умолчанию")
    assert task is not None
    assert task.planned_for is None

    backlog = await client.get("/backlog")
    assert "Задача по умолчанию" in backlog.text
    dashboard = await client.get("/")
    assert "Задача по умолчанию" not in dashboard.text


@pytest.mark.asyncio
async def test_edit_form_moves_task_between_day_and_backlog(client):
    """Форма редактирования умеет и снять задачу с дня, и поставить её на день."""
    await client.post("/tasks/web/create", data={"title": "Туда-сюда", "where": "today"})
    task = await _get("Туда-сюда")
    assert task.planned_for == date.today()

    await client.post(
        f"/tasks/web/{task.id}/edit", data={"title": "Туда-сюда", "where": "backlog"}
    )
    task = await _get("Туда-сюда")
    assert task.planned_for is None, "«В бэклог» из формы не снял задачу с дня"

    await client.post(
        f"/tasks/web/{task.id}/edit", data={"title": "Туда-сюда", "where": "today"}
    )
    task = await _get("Туда-сюда")
    assert task.planned_for == date.today(), "«Сегодня» из формы не поставил задачу в день"


@pytest.mark.asyncio
async def test_edit_without_where_does_not_touch_planned_for(client):
    """Правка без поля where не должна молча выкидывать задачу из дня."""
    await client.post("/tasks/web/create", data={"title": "Правка без where", "where": "today"})
    task = await _get("Правка без where")

    await client.post(f"/tasks/web/{task.id}/edit", data={"title": "Правка без where"})

    task = await _get("Правка без where")
    assert task.planned_for == date.today()


@pytest.mark.asyncio
async def test_backlog_is_split_into_work_and_personal_halves(client, db):
    """Работа и Личное — две половины: каждая задача попадает в свою."""
    work = await _make_category(db, "Работа")
    await _make_category(db, "СберМобайл тест", parent_id=work.id, is_global=0)
    work_sub = (
        await db.execute(select(Category).where(Category.name == "СберМобайл тест"))
    ).scalar_one()
    personal = await _make_category(db, "Личное")
    db.add_all(
        [
            Task(title="Работа задача A", category_id=work_sub.id, status="новая",
                 source="web", item_kind="task"),
            Task(title="Личная задача B", category_id=personal.id, status="новая",
                 source="web", item_kind="task"),
        ]
    )
    await db.commit()

    html = (await client.get("/backlog")).text

    assert "Работа задача A" in html and "Личная задача B" in html
    # У работы отрисована полная ветка категории, а не только подкатегория.
    assert "Работа → СберМобайл тест" in html
    head = '<h2 class="text-base font-bold text-white">%s</h2>'
    work_head = html.index(head % "Работа")
    personal_head = html.index(head % "Личное")
    work_at = html.index("Работа задача A")
    personal_at = html.index("Личная задача B")
    # порядок в документе: заголовок «Работа», работа, заголовок «Личное», личное
    assert work_head < work_at < personal_head < personal_at


@pytest.mark.asyncio
async def test_delete_task_removes_subtasks_and_dependent_rows(client, db):
    """Корзина удаляет задачу насовсем: подзадачи уходят, ссылки подчищаются."""
    root = Task(title="Корень на снос", status="новая", source="web", item_kind="task")
    db.add(root)
    await db.flush()
    db.add_all(
        [
            Task(title="Дитя 1", parent_task_id=root.id, status="новая", source="web",
                 item_kind="task"),
            Task(title="Дитя 2", parent_task_id=root.id, status="новая", source="web",
                 item_kind="task"),
        ]
    )
    impact = CareerImpact(task_id=root.id, impact_description="текст", original_title="Корень на снос")
    shot = Screenshot(file_path="/tmp/x.png", task_id=root.id)
    db.add_all([impact, shot])
    await db.commit()
    root_id, impact_id, shot_id = root.id, impact.id, shot.id

    resp = await client.post(f"/backlog/{root_id}/delete-task")
    assert resp.status_code == 200

    async with async_session() as s:
        assert (await s.execute(select(Task).where(Task.id == root_id))).scalar_one_or_none() is None
        kids = (await s.execute(select(Task).where(Task.parent_task_id == root_id))).scalars().all()
        assert kids == [], "подзадачи остались в базе"
        assert (await s.execute(select(CareerImpact).where(CareerImpact.id == impact_id))).scalar_one_or_none() is None
        # Скриншот ценен сам по себе: строка остаётся, ссылка снимается.
        leftover = (await s.execute(select(Screenshot).where(Screenshot.id == shot_id))).scalar_one_or_none()
        assert leftover is not None and leftover.task_id is None

    # за собой прибираем: снимок не удаляет тест-фикстура
    async with async_session() as s:
        await s.execute(delete(Screenshot).where(Screenshot.id == shot_id))
        await s.commit()


@pytest.mark.asyncio
async def test_archive_shows_root_tasks_only(client, db):
    """Архив — место для закрытых корневых задач; подзадачи туда не сыпятся."""
    root = Task(title="Корень в архиве", status="выполнена", source="web",
                item_kind="task", is_archived=True)
    db.add(root)
    await db.flush()
    db.add(Task(title="Подзадача в архиве", parent_task_id=root.id, status="выполнена",
                source="web", item_kind="task", is_archived=True))
    await db.commit()

    html = (await client.get("/archive")).text

    assert "Корень в архиве" in html
    assert "Подзадача в архиве" not in html


@pytest.mark.asyncio
async def test_take_subtask_to_day_from_backlog(client, db):
    """Подзадачу берут в день саму по себе: у дня появляется она, а не весь родитель."""
    parent = Task(title="Большая задача", status="новая", source="web", item_kind="task")
    db.add(parent)
    await db.flush()
    sub = Task(title="Взять подзадачу", parent_task_id=parent.id, status="новая",
               source="web", item_kind="task")
    db.add(sub)
    await db.commit()
    sub_id, parent_id = sub.id, parent.id

    resp = await client.post(f"/backlog/{sub_id}/plan-today")
    assert resp.status_code == 200

    async with async_session() as s:
        sub = (await s.execute(select(Task).where(Task.id == sub_id))).scalar_one()
        parent = (await s.execute(select(Task).where(Task.id == parent_id))).scalar_one()
    assert sub.planned_for == date.today(), "подзадача не попала в день"
    assert parent.planned_for is None, "вместе с подзадачей в день уехал весь родитель"


@pytest.mark.asyncio
async def test_quick_add_where_today_and_backlog(client):
    """Строка быстрого ввода: выбор «Сегодня» и «В бэклог» работает по-разному."""
    await client.post("/tasks/create", data={"title": "Быстрая в день", "where": "today"})
    await client.post("/tasks/create", data={"title": "Быстрая в бэклог", "where": "backlog"})

    in_day = await _get("Быстрая в день")
    in_backlog = await _get("Быстрая в бэклог")
    assert in_day.planned_for == date.today()
    assert in_backlog.planned_for is None
