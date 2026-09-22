"""Задача с пустым is_archived не должна быть невидимой.

В базе Веры 29 задач (все source='hermes') лежали с незаполненным is_archived:
сравнение `is_archived == False` в SQL отбрасывает NULL, поэтому задачи не
попадали ни в списки, ни в счётчики категорий — «категории не связаны с
задачами», «количество не обновляется».
"""

import re
from datetime import datetime

import pytest
from sqlalchemy import select, text

from app.models.category import Category
from app.models.task import Task

pytestmark = pytest.mark.asyncio


async def _task_from_integration(db, title="Задача из интеграции", category_id=None) -> Task:
    """Задача, как её писала интеграция: мимо ORM, колонка не заполнена."""
    task = Task(title=title, category_id=category_id, source="hermes")
    db.add(task)
    await db.commit()
    await db.refresh(task)
    await db.execute(
        text("UPDATE tasks SET is_archived = NULL WHERE id = :i"), {"i": task.id}
    )
    await db.commit()
    return task


async def _global_task_category(db) -> Category:
    result = await db.execute(
        select(Category)
        .where(Category.is_global == True, Category.type == "task")  # noqa: E712
        .order_by(Category.id)
        .limit(1)
    )
    category = result.scalar_one_or_none()
    assert category is not None, "нужна хотя бы одна глобальная категория задач"
    return category


async def test_category_counter_separates_completed_from_active(client, db):
    """Завершённая задача уходит из активных, но остаётся в общем числе категории."""
    category = await _global_task_category(db)
    await _task_from_integration(db, title="Доделать отчёт", category_id=category.id)
    done = Task(
        title="Отчёт сдан",
        source="web",
        category_id=category.id,
        status="выполнена",
        completed_at=datetime(2026, 9, 1, 12, 0),
    )
    db.add(done)
    await db.commit()

    response = await client.get("/backlog?view=categories")

    assert response.status_code == 200
    assert "1 / 2 задач" in response.text, "активная одна из двух — вторая завершена"


async def test_backlog_shows_task_with_empty_archived_flag(client, db):
    task = await _task_from_integration(db, title="Позвонить в банк из Hermes")

    response = await client.get("/backlog")

    assert response.status_code == 200
    assert task.title in response.text


async def test_api_list_shows_task_with_empty_archived_flag(client, db):
    task = await _task_from_integration(db, title="Задача из Hermes для API")

    response = await client.get("/api/tasks")

    assert response.status_code == 200
    titles = [item["title"] for item in response.json()]
    assert task.title in titles


async def test_category_counter_counts_task_with_empty_archived_flag(client, db):
    category = await _global_task_category(db)
    await _task_from_integration(db, title="Задача в категории", category_id=category.id)

    response = await client.get("/backlog?view=categories")

    assert response.status_code == 200
    assert "1 / 1 задач" in response.text, "счётчик категории должен видеть задачу из интеграции"
    # на странице имя идёт через фильтр noemoji: «🏢 Работа» → «Работа»
    plain_name = re.sub(r"[^\w\s]+", "", category.name).strip()
    assert plain_name in response.text


async def test_archived_task_stays_hidden_from_backlog(client, db):
    """Обратная проверка: правка не должна вытаскивать архив в бэклог."""
    task = Task(title="Задача в архиве", source="web", is_archived=True)
    db.add(task)
    await db.commit()

    response = await client.get("/backlog")

    assert response.status_code == 200
    assert task.title not in response.text


async def test_category_counter_shows_active_and_total(client, db):
    """Плашка категории показывает «активные / всего»: архив входит в общее число."""
    category = await _global_task_category(db)
    await _task_from_integration(db, title="активная в категории", category_id=category.id)
    db.add(Task(title="архивная в категории", category_id=category.id, source="web", is_archived=True))
    await db.commit()

    response = await client.get("/backlog?view=categories")

    assert response.status_code == 200
    assert "1 / 2 задач" in response.text, "в общее число входит и архивная задача"


async def test_task_is_active_covers_empty_and_zero_but_not_archive(db):
    """Фильтр «не в архиве»: пустое значение и ноль — активные, единица — архив."""
    from app.models.task import task_is_active

    await _task_from_integration(db, title="пустой флаг")
    db.add(Task(title="нулевой флаг", source="web", is_archived=False))
    db.add(Task(title="задача в архиве", source="web", is_archived=True))
    await db.commit()

    result = await db.execute(select(Task.title).where(task_is_active()))
    titles = set(result.scalars().all())

    assert "пустой флаг" in titles
    assert "нулевой флаг" in titles
    assert "задача в архиве" not in titles
