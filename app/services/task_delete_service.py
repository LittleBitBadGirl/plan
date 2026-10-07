"""Снос задачи из базы: подзадачи и ссылки из других таблиц.

SQLite в этом проекте поднимается без ``PRAGMA foreign_keys``, поэтому база не
удаляет и не обнуляет ссылки сама. Обычный ``db.delete(task)`` оставлял бы в
``career_impacts`` и ``screenshots`` ссылки на уже несуществующую задачу
(находка критика 07.10.2026: в локальной копии прод-базы таких ссылок 0, то есть
ошибка не проявлялась, но накопилась бы).

Поэтому снос идёт только через этот сервис: он удаляет подзадачи и подчищает
зависимые строки. Архив сюда не ходит — архив это ``is_archived = True``.
"""

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.impact import CareerImpact
from app.models.screenshot import Screenshot
from app.models.task import Task


async def _descendant_ids(db: AsyncSession, root_id: int) -> list[int]:
    """Собрать id всего поддерева, а не только прямых детей.

    Подзадача может иметь свою подзадачу (API это разрешает), а внуки не должны
    оставаться сиротами.
    """
    ids: list[int] = []
    frontier = [root_id]
    while frontier:
        kids = (
            await db.execute(select(Task.id).where(Task.parent_task_id.in_(frontier)))
        ).scalars().all()
        ids.extend(kids)
        frontier = list(kids)
    return ids


async def delete_tasks_hard(db: AsyncSession, root: Task) -> list[int]:
    """Удалить задачу со всем поддеревом подзадач и подчистить ссылки.

    Возвращает список id удалённых задач (для логов и тестов).
    """
    ids = [root.id, *await _descendant_ids(db, root.id)]

    # Скриншотам даём выжить: они ценны сами по себе, поэтому ссылку снимаем,
    # а не удаляем строку. Впечатления карьеры — производная от задачи, уходят
    # вместе с ней.
    await db.execute(
        update(Screenshot).where(Screenshot.task_id.in_(ids)).values(task_id=None)
    )
    await db.execute(delete(CareerImpact).where(CareerImpact.task_id.in_(ids)))

    for row in (
        await db.execute(select(Task).where(Task.id.in_(ids)))
    ).scalars().all():
        await db.delete(row)

    return ids
