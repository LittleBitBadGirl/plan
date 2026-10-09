"""Пул дня: в «сегодня» задача попадает только если её взяли из бэклога.

Решение Веры 07.10.2026: утром она открывает планировщик, набирает минимум
5 задач из бэклога, днём добирает по мере надобности (лимита больше нет),
вечером всё незакрытое само возвращается в бэклог. Признак «взято на день» —
колонка tasks.planned_for.

Почему отдельная колонка, а не due_date: due_date — это срок. Если писать
«взято на сегодня» в due_date, то у задачи с реальным будущим сроком срок
затирается, а вернуть её в бэклог можно только стерев дату. planned_for
означает ровно одно: на какой день задачу взяли руками.

Регулярные задачи (source='recurring') остаются на старой механике: они
появляются в своём дне по due_date и в счётчик «взято из бэклога» не идут.
"""
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.task import Task, task_is_active
from app.services.postpones_service import WORK_CATEGORY_NAMES, apply_backlog_return

# Минимум, который Вера набирает утром. Это порог ритуала, а не лимит дня.
MIN_DAY_TASKS = 5


def open_task_filter():
    """Задача, с которой ещё можно что-то делать (не архив, не закрыта)."""
    return and_(task_is_active(), Task.status.in_(["новая", "в_работе"]))


def is_regular():
    return Task.source == "recurring"


def day_filter(day: date):
    """Что показывает день: взятое руками + вхождения регулярных."""
    return or_(
        Task.planned_for == day,
        and_(is_regular(), Task.due_date == day),
    )


def planned_ahead_filter(today: Optional[date] = None):
    """Ждёт в бэклоге: день не назначен или назначен на будущее.

    Решение Веры 09.10.2026: день можно проставить прямо из бэклога
    (⋯ → «Поставить дату»), не при создании задачи. Задача с будущей датой
    остаётся видимой в бэклоге с подписью «на 15.10» — иначе она исчезла бы с
    глаз до самого дня. Наступил этот день — planned_for == today, задача сама
    уходит в день, а из бэклога пропадает: условие «будущее» перестаёт
    выполняться.
    """
    today = today or date.today()
    return or_(Task.planned_for.is_(None), Task.planned_for > today)


def backlog_filter():
    """Бэклог: активное, не взятое на день (либо взятое на будущий день) и не регулярное.

    Подзадачи тоже живут в бэклоге — но показываются внутри родителя.
    """
    return and_(
        open_task_filter(),
        planned_ahead_filter(),
        Task.source.is_distinct_from("recurring"),
        Task.item_kind == "task",
    )


async def take_to_day(db: AsyncSession, task: Task, day: Optional[date] = None) -> Task:
    """Взять задачу из бэклога на день.

    Счётчик переносов не сбрасываем: в новой механике postpones означает
    «сколько раз брал и не доделал», и обнуление убило бы и статистику, и
    пометку «хроническая».
    """
    task.planned_for = day or date.today()
    await db.flush()
    return task


async def return_to_backlog(db: AsyncSession, task: Task) -> Task:
    """Вернуть задачу в бэклог руками (крестик в дне). Переносы не трогаем."""
    task.planned_for = None
    await db.flush()
    return task


async def return_unfinished_to_backlog(db: AsyncSession, day: Optional[date] = None) -> dict:
    """Ночной возврат: всё, что вечером осталось незакрытым, уходит в бэклог.

    Заменяет прежний ролловер (который тащил все незакрытые задачи в сегодня
    и делал из дня свалку). Здесь наоборот: задача покидает день, а счётчик
    переносов растёт — по нему видно, что брали и не доделали.
    """
    today = day or date.today()

    result = await db.execute(
        select(Task)
        .options(selectinload(Task.category))
        .where(
            open_task_filter(),
            Task.planned_for.is_not(None),
            Task.planned_for < today,
        )
    )
    tasks = list(result.scalars().all())

    returned = 0
    new_chronic = 0
    for task in tasks:
        was_chronic = bool(task.chronic_task)
        cat_name = task.category.name if task.category else ""
        apply_backlog_return(
            task,
            task.planned_for,
            today,
            is_work_category=cat_name in WORK_CATEGORY_NAMES,
        )
        returned += 1
        if task.chronic_task and not was_chronic:
            new_chronic += 1

    await db.flush()
    return {"moved": returned, "returned": returned, "new_chronic": new_chronic}


async def count_taken(db: AsyncSession, day: Optional[date] = None) -> int:
    """Сколько задач взято на день (для счётчика «взято N из 5»).

    Считаем и уже закрытые: это число про взятые обязательства, а не про
    остаток работы. Иначе после закрытия пары задач счётчик падал бы обратно
    под порог, ритуал «набери 5» возвращался бы посреди дня, а Вера видела бы
    «0 из 5» после пяти взятых задач — она это отдельно отмечала.
    """
    today = day or date.today()
    result = await db.execute(
        select(func.count(Task.id)).where(
            Task.planned_for == today,
            Task.item_kind == "task",
        )
    )
    return result.scalar() or 0


async def count_backlog(db: AsyncSession) -> int:
    """Сколько корневых задач лежит в бэклоге (для сводки).

    Считаем и задачи с будущей датой: они лежат в бэклоге с подписью, и число
    на экране обязано совпадать со списком.
    """
    # Фильтр ровно такой же, как у списка бэклога (_load_backlog): иначе на
    # экране соседствовали два разных числа про одно и то же («Задачи 60» и
    # «56 в бэклоге»).
    result = await db.execute(
        select(func.count(Task.id)).where(
            task_is_active(),
            planned_ahead_filter(),
            or_(Task.parent_task_id.is_(None), Task.parent_task_id == 0),
            Task.source.is_distinct_from("recurring"),
            Task.item_kind == "task",
        )
    )
    return result.scalar() or 0


def counter_label(taken: int) -> str:
    """Как читается счётчик дня.

    До пяти — «взято 3 из 5» (видно, сколько осталось набрать). С пяти и
    больше знаменатель не нужен: это не лимит, а порог ритуала, поэтому
    просто «взято 6», «взято 7».
    """
    if taken < MIN_DAY_TASKS:
        return f"взято {taken} из {MIN_DAY_TASKS}"
    if taken == MIN_DAY_TASKS:
        return f"взято {taken}, минимум набран"
    return f"взято {taken}"
