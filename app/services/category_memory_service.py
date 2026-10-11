"""Память категорий регулярных задач.

Решение Веры 11.10.2026: если задаче из регулярных один раз присвоили
категорию, второй раз про неё спрашивать нельзя.

Почему категория уходит в ШАБЛОН, а не остаётся в задаче: вхождение из
регулярных живёт один день — ночью просроченные уходят в архив
(`app/services/recurring_service.py`), а утром генератор создаёт новое
вхождение с той категорией, что записана в шаблоне. Пока категория жила
только в задаче, каждое утро рождалась новая «без категории», и
крон-категоризатор (`planner-category-review`, 12:00 и 17:00 МСК) присылал
то же название на подтверждение снова и снова — например «английский!!».
"""
import logging
from typing import Optional, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recurring import RecurringTask
from app.models.task import Task

logger = logging.getLogger(__name__)

RECURRING_SOURCE = "recurring"


async def remember_category(
    db: AsyncSession,
    task: Task,
    previous_category_id: Optional[int] = None,
) -> Optional[RecurringTask]:
    """Запомнить категорию задачи в её шаблоне; вернуть изменённый шаблон.

    `previous_category_id` — категория, которая была у задачи ДО правки. По ней
    выбирается нужный шаблон, когда под одним названием их несколько: создание
    шаблона-дубля разрешено, если отличается категория (см. `/recurring/create`).
    Один шаблон под названием — правим его, несколько и предыдущая категория
    неизвестна — не гадаем и ничего не меняем.
    """
    if str(task.source or "") != RECURRING_SOURCE:
        return None
    new_category_id = task.category_id
    if new_category_id is None:
        return None

    result = await db.execute(
        select(RecurringTask).where(
            RecurringTask.title == task.title,
            RecurringTask.is_active == True,  # noqa: E712 — как в остальных выборках проекта
        )
    )
    templates = list(result.scalars().all())
    if not templates:
        return None

    template = None
    if previous_category_id is not None:
        template = next(
            (t for t in templates if t.category_id == previous_category_id), None
        )
    if template is None and len(templates) == 1:
        template = templates[0]
    if template is None:
        logger.info(
            "Категория задачи %s не запомнена: под названием %r несколько шаблонов",
            task.id,
            task.title,
        )
        return None
    stored_category_id = cast("Optional[int]", template.category_id)
    if stored_category_id is not None and stored_category_id == new_category_id:
        return None

    template.category_id = new_category_id
    await db.flush()
    return template
