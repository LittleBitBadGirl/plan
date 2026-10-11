"""Что крон-категоризатор спрашивает у Веры.

Правило живёт здесь, а не в скрипте: `scripts/cat_review_pending.py` — тонкая
обёртка, и так его можно проверить тестами.

Решение Веры 11.10.2026: задачи из регулярных, которым категорию присвоили
один раз, больше не показывать. Категория при этом запоминается в шаблоне
(`app/services/category_memory_service.py`), поэтому отсекаем задачи, чьё
название совпадает с активным шаблоном, у которого категория уже есть.

Шаблон БЕЗ категории задачи не отсекает: именно на такой задаче Вера впервые
и говорит, куда её отнести — после ответа категория уезжает в шаблон, и
следующее вхождение в ревью уже не попадёт.
"""
from typing import List, Tuple

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.recurring import RecurringTask
from app.models.task import Task

PENDING_LIMIT = 15


def _categorized_template_titles():
    """Названия активных шаблонов, у которых категория уже проставлена."""
    return select(RecurringTask.title).where(
        RecurringTask.is_active == True,  # noqa: E712 — как в остальных выборках проекта
        RecurringTask.category_id.is_not(None),
    )


def pending_filter():
    """Задача без категории, о которой ещё есть смысл спрашивать Веру."""
    return and_(
        Task.category_id.is_(None),
        Task.item_kind == "task",
        Task.parent_task_id.is_(None),
        Task.title.notin_(_categorized_template_titles()),
    )


async def load_pending_tasks(db: AsyncSession, limit: int = PENDING_LIMIT) -> List[Tuple[int, str]]:
    """Пары (id, title) задач, которые крон-категоризатор показывает Вере."""
    result = await db.execute(
        select(Task.id, Task.title)
        .where(pending_filter())
        .order_by(Task.id.desc())
        .limit(limit)
    )
    return [(row[0], row[1]) for row in result.all()]


async def load_leaf_categories(db: AsyncSession) -> List[Tuple[int, str]]:
    """Категории-листья для выбора: глобальные группы не предлагаем."""
    result = await db.execute(
        select(Category.id, Category.name)
        .where(
            Category.type == "task",
            Category.is_global == False,  # noqa: E712 — как в остальных выборках проекта
        )
        .order_by(Category.name)
    )
    return [(row[0], row[1]) for row in result.all()]
