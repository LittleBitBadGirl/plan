"""Выходные: свой счёт — «закрыто сегодня», без ритуала и без «дня без хвоста».

Решение Веры 11.10.2026: на выходном она не набирает пятёрку — на экране сразу
весь личный бэклог. Значит «взято 2 из 5» и «прогресс 2/4» считают не тот день,
который она прожила: за воскресенье закрыто девять задач, а счётчик показывал
две. Вместо них — «закрыто сегодня», полоса прогресса дня на выходном не
рисуется, а поздравление «день без хвоста» не показывается вовсе (Вера: «может
быть только если я весь бэклог закрою — лучше не показывать вообще»).
"""
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

from app.models.category import Category
from app.models.task import Task
from app.services.day_pool_service import count_closed
from app.web.deps import (
    build_day_pool_context,
    render_day_pool_block,
    render_day_win_block,
)

SUNDAY = date(2026, 10, 11)
SATURDAY = date(2026, 10, 10)
WEDNESDAY = date(2026, 10, 7)


def _closed_at(day: date) -> datetime:
    """Полдень локального дня в UTC без пояса — так completed_at лежит в базе."""
    local_noon = datetime.combine(day, time(12, 0)).astimezone()
    return local_noon.astimezone(timezone.utc).replace(tzinfo=None)


async def _categories(db):
    work = Category(name="Работа", type="task", is_global=1)
    personal = Category(name="Личное-тест-выходной", type="task", is_global=0)
    db.add_all([work, personal])
    await db.flush()
    return work, personal


async def _closed_task(db, title: str, day: date, category_id=None) -> Task:
    task = Task(
        title=title,
        category_id=category_id,
        status="выполнена",
        completed_at=_closed_at(day),
        is_archived=True,
        source="web",
        item_kind="task",
    )
    db.add(task)
    await db.flush()
    return task


async def test_count_closed_splits_days_by_local_date(db):
    await _closed_task(db, "закрыта в воскресенье", SUNDAY)
    await _closed_task(db, "закрыта в субботу", SATURDAY)
    await _closed_task(db, "закрыта в понедельник", SUNDAY + timedelta(days=1))

    assert await count_closed(db, SUNDAY) == 1
    assert await count_closed(db, SATURDAY) == 1


async def test_count_closed_skips_hidden_work_categories(db):
    work, personal = await _categories(db)
    await _closed_task(db, "личная закрыта", SUNDAY, category_id=personal.id)
    await _closed_task(db, "рабочая закрыта", SUNDAY, category_id=work.id)
    await _closed_task(db, "без категории закрыта", SUNDAY)

    assert await count_closed(db, SUNDAY, work_ids={work.id}) == 2
    assert await count_closed(db, SUNDAY) == 3


async def test_weekend_pool_shows_closed_today_instead_of_ritual(db):
    await _closed_task(db, "закрыта в воскресенье 1", SUNDAY)
    await _closed_task(db, "закрыта в воскресенье 2", SUNDAY)

    pool = await build_day_pool_context(db, request=None, today=SUNDAY)

    assert pool["weekend"] is True
    assert pool["closed_today"] == 2
    assert pool["show_ritual"] is False

    html = render_day_pool_block(pool)
    assert "закрыто сегодня" in html
    assert "на день из 5" not in html
    assert "Прогресс" not in html


async def test_weekend_pool_skips_work_when_work_is_hidden(db):
    work, personal = await _categories(db)
    await _closed_task(db, "личная закрыта", SUNDAY, category_id=personal.id)
    await _closed_task(db, "рабочая закрыта", SUNDAY, category_id=work.id)

    request = SimpleNamespace(url=SimpleNamespace(path="/"), cookies={})
    pool = await build_day_pool_context(db, request=request, today=SUNDAY)

    assert pool["weekend"] is True
    assert pool["closed_today"] == 1


async def test_weekday_pool_keeps_ritual_counter(db):
    """В будни всё как было: ритуал, «на день из 5» и прогресс дня."""
    await _closed_task(db, "закрыта в среду", WEDNESDAY)

    pool = await build_day_pool_context(db, request=None, today=WEDNESDAY)

    assert pool["weekend"] is False
    assert pool["show_ritual"] is True

    html = render_day_pool_block(pool)
    assert "на день из 5" in html
    assert "Прогресс" in html


async def test_day_win_block_is_hidden_without_state(db):
    """На выходном состояния дня нет — блок «день без хвоста» скрыт."""
    html = render_day_win_block(None)

    assert "Ты закрыла все задачи на сегодня" not in html
    assert "hidden" in html


async def test_weekend_counted_without_work_cookie_hides_work_too(db):
    """Cookie «показать рабочие» на выходном возвращает рабочие в счёт."""
    work, personal = await _categories(db)
    await _closed_task(db, "личная закрыта", SUNDAY, category_id=personal.id)
    await _closed_task(db, "рабочая закрыта", SUNDAY, category_id=work.id)

    request = SimpleNamespace(url=SimpleNamespace(path="/"), cookies={"show_work": "1"})
    pool = await build_day_pool_context(db, request=request, today=SUNDAY)

    assert pool["closed_today"] == 2
