"""Категория регулярной задачи запоминается в шаблоне.

Решение Веры 11.10.2026: если задаче из регулярных категорию присвоили один
раз, второй раз про неё не спрашивать. Категория при этом должна уехать в
ШАБЛОН (см. app/services/category_memory_service.py), иначе следующее
вхождение снова родится «без категории» и вернётся в крон-ревью.
"""
from datetime import date

import pytest

from app.models.category import Category
from app.models.recurring import RecurringTask
from app.models.task import Task
from app.services.category_memory_service import remember_category
from app.services.categorize_pending_service import load_pending_tasks

# Без маркера pytest-asyncio (strict) не запускает async-тесты.
pytestmark = pytest.mark.asyncio


async def _category(db, name: str) -> Category:
    cat = Category(name=name, is_global=False, type="task")
    db.add(cat)
    await db.flush()
    return cat


async def _template(db, title: str = "английский!!", category_id=None, is_active: bool = True):
    tpl = RecurringTask(
        title=title,
        recurrence_type="daily",
        start_date=date(2026, 1, 1),
        is_active=is_active,
        category_id=category_id,
    )
    db.add(tpl)
    await db.flush()
    return tpl


async def _task(db, title: str = "английский!!", source: str = "recurring", category_id=None):
    task = Task(
        title=title,
        source=source,
        category_id=category_id,
        due_date=date.today(),
        status="новая",
    )
    db.add(task)
    await db.flush()
    return task


# ─── запоминание категории в шаблоне ────────────────────────────────────────

async def test_category_of_recurring_task_lands_in_template(db):
    cat = await _category(db, "Курсы-тест")
    tpl = await _template(db)
    task = await _task(db)

    task.category_id = cat.id
    touched = await remember_category(db, task, None)

    assert touched is not None
    assert touched.id == tpl.id
    assert tpl.category_id == cat.id


async def test_non_recurring_task_does_not_touch_templates(db):
    cat = await _category(db, "Курсы-тест-2")
    tpl = await _template(db, title="обычная задача")
    task = await _task(db, title="обычная задача", source="web")

    task.category_id = cat.id
    assert await remember_category(db, task, None) is None
    assert tpl.category_id is None


async def test_task_without_category_does_not_touch_templates(db):
    cat = await _category(db, "Курсы-тест-3")
    tpl = await _template(db)
    task = await _task(db)

    task.category_id = None
    assert await remember_category(db, task, None) is None
    assert tpl.category_id is None
    assert cat.id


async def test_paused_template_is_not_touched(db):
    cat = await _category(db, "Курсы-тест-4")
    paused = await _template(db, is_active=False)
    task = await _task(db)

    task.category_id = cat.id
    assert await remember_category(db, task, None) is None
    assert paused.category_id is None


async def test_same_category_twice_is_noop(db):
    cat = await _category(db, "Курсы-тест-5")
    await _template(db, category_id=cat.id)
    task = await _task(db, category_id=cat.id)

    assert await remember_category(db, task, None) is None


async def test_two_templates_same_title_uses_previous_category(db):
    """Дубли шаблонов разрешены при разной категории — правим тот, что был у задачи."""
    first = await _category(db, "Курсы-тест-6")
    second = await _category(db, "Курсы-тест-7")
    tpl_first = await _template(db, category_id=first.id)
    tpl_second = await _template(db)

    task = await _task(db, category_id=first.id)
    task.category_id = second.id
    touched = await remember_category(db, task, first.id)

    assert touched is not None and touched.id == tpl_first.id
    assert tpl_first.category_id == second.id
    assert tpl_second.category_id is None


async def test_two_templates_same_title_without_previous_is_skipped(db):
    """Не знаем, из какого шаблона пришла задача — не гадаем."""
    first = await _category(db, "Курсы-тест-8")
    await _template(db)
    await _template(db)

    task = await _task(db, category_id=first.id)
    assert await remember_category(db, task, None) is None


# ─── что попадает в крон-ревью ─────────────────────────────────────────────

async def test_pending_hides_task_of_categorized_template(db):
    """«Английский»: категорию поставили один раз — в ревью больше не приходить."""
    cat = await _category(db, "Курсы-тест-9")
    await _template(db, title="английский!!", category_id=cat.id)
    await _task(db, title="английский!!")

    pending = await load_pending_tasks(db)
    assert "английский!!" not in [title for _, title in pending]


async def test_pending_keeps_task_of_uncategorized_template(db):
    """Шаблон без категории — именно на такой задаче Вера впервые и отвечает."""
    await _template(db, title="выкинуть гипрок")
    await _task(db, title="выкинуть гипрок")

    pending = await load_pending_tasks(db)
    assert "выкинуть гипрок" in [title for _, title in pending]


async def test_pending_hides_task_that_already_has_category(db):
    cat = await _category(db, "Курсы-тест-10")
    await _task(db, title="обычная без шаблона", source="web", category_id=cat.id)

    pending = await load_pending_tasks(db)
    assert "обычная без шаблона" not in [title for _, title in pending]
