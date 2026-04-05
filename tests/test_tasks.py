import pytest
from datetime import date

from app.models.task import Task


@pytest.mark.asyncio
async def test_create_task(db_session):
    """Тест создания задачи"""
    task = Task(
        title="Тестовая задача",
        description="Описание задачи",
        status="новая",
        priority="средний",
        due_date=date(2026, 4, 5),
        source="web",
    )
    db_session.add(task)
    await db_session.flush()
    await db_session.refresh(task)

    assert task.id is not None
    assert task.title == "Тестовая задача"
    assert task.description == "Описание задачи"
    assert task.status == "новая"
    assert task.is_archived is False
    assert task.postpones == 0
    assert task.chronic_task is False


@pytest.mark.asyncio
async def test_soft_delete_task(db_session):
    """Тест мягкого удаления (архивирования) задачи"""
    task = Task(
        title="Задача для удаления",
        status="новая",
        source="web",
    )
    db_session.add(task)
    await db_session.flush()
    await db_session.refresh(task)

    task_id = task.id
    assert task.is_archived is False

    # Архивирование
    task.is_archived = True
    await db_session.flush()

    # Проверка что задача заархивирована
    result = await db_session.get(Task, task_id)
    assert result.is_archived is True
