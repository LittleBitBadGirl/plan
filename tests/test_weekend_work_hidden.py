"""Выходные: рабочие задачи скрыты, кнопка возвращает их на экран.

Вера не хочет возвращаться в работу на выходных. Правило: в субботу и воскресенье
задачи рабочих категорий не показываются и не считаются в счётчиках, а кнопка
«Показать рабочие» (cookie show_work=1) возвращает их. В понедельник всё как обычно.

Рабочей считается и категория из списка, и её подкатегория — у Веры клиенты висят
подкатегориями под «Работа», по имени подкатегории их не отловить.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.category import Category
from app.models.task import Task
from app.web.deps import _today_roots_filter


async def _make_tasks(db, due_date=None):
    """Три задачи на сегодня: рабочая (через подкатегорию клиента), личная, без категории."""
    work_parent = Category(name="Работа", type="task", is_global=1)
    personal = Category(name="Личное", type="task", is_global=1)
    db.add_all([work_parent, personal])
    await db.flush()

    client_sub = Category(
        name="СберМобайл проверка",
        parent_id=work_parent.id,
        type="task",
        is_global=0,
    )
    db.add(client_sub)
    await db.flush()

    today = due_date or date.today()
    db.add_all(
        [
            Task(title="Рабочая", category_id=client_sub.id, due_date=today, status="новая", source="web", item_kind="task"),
            Task(title="Личная", category_id=personal.id, due_date=today, status="новая", source="web", item_kind="task"),
            Task(title="Без категории", category_id=None, due_date=today, status="новая", source="web", item_kind="task"),
        ]
    )
    await db.commit()


async def _visible_titles(db, today, hide_work):
    rows = (
        await db.execute(select(Task).where(*_today_roots_filter(today, hide_work)))
    ).scalars().all()
    return {row.title for row in rows}


@pytest.mark.asyncio
async def test_weekend_hides_work_tasks(db):
    await _make_tasks(db)

    titles = await _visible_titles(db, date.today(), hide_work=True)

    assert titles == {"Личная", "Без категории"}


@pytest.mark.asyncio
async def test_without_hiding_everything_is_visible(db):
    """Правило включается только в выходные: в будни видно всё."""
    await _make_tasks(db)

    titles = await _visible_titles(db, date.today(), hide_work=False)

    assert titles == {"Рабочая", "Личная", "Без категории"}


@pytest.mark.asyncio
async def test_subcategory_of_work_parent_is_work(db):
    """Клиентская подкатегория под «Работа» тоже считается рабочей."""
    await _make_tasks(db)

    titles = await _visible_titles(db, date.today(), hide_work=True)

    assert "Рабочая" not in titles


@pytest.mark.asyncio
async def test_weekend_filter_holds_after_actions(client, db, monkeypatch):
    """Главная проверка: фильтр работает и в живых обновлениях, а не только при отрисовке.

    Критик нашёл, что после любого действия (счётчики по /dashboard/today-stats,
    перерисовка списка после создания задачи) числа и список считались БЕЗ фильтра,
    и в субботу рабочие задачи возвращались на экран.
    """
    import re

    from app.web.routes import dashboard as dashboard_module
    import app.web.deps as deps_module

    await _make_tasks(db, due_date=date(2026, 9, 19))
    monkeypatch.setattr(dashboard_module, "date", _Saturday)
    monkeypatch.setattr(deps_module, "date", _Saturday)
    # иначе создаваемая задача ляжет на настоящую пятницу и не попадёт в субботний список
    import app.web.routes.tasks as tasks_module

    monkeypatch.setattr(tasks_module, "date", _Saturday)

    stats = await client.get("/dashboard/today-stats")
    assert stats.status_code == 200
    active = re.search(r'id="today-stats-counter"[^>]*>(\d+)<', stats.text)
    # на экране две задачи: личная и без категории (рабочая скрыта) — в числах те же две
    assert active is not None and int(active.group(1)) == 2

    created = await client.post("/tasks/create", data={"title": "Задача в субботу", "category_id": ""})
    assert created.status_code == 200
    assert "Рабочая" not in created.text
    assert "Личная" in created.text
    assert "Задача в субботу" in created.text


class _Saturday(date):
    @classmethod
    def today(cls):
        # 19.09.2026 — суббота
        return date(2026, 9, 19)


class _Monday(date):
    @classmethod
    def today(cls):
        # 21.09.2026 — понедельник
        return date(2026, 9, 21)


@pytest.mark.asyncio
async def test_dashboard_hides_work_on_saturday_and_shows_with_cookie(client, db, monkeypatch):
    from app.web.routes import dashboard as dashboard_module

    # Задачи ставим на тот же день, который «видит» дашборд.
    await _make_tasks(db, due_date=date(2026, 9, 19))

    monkeypatch.setattr(dashboard_module, "date", _Saturday)
    hidden = await client.get("/")
    assert hidden.status_code == 200
    assert "Рабочая" not in hidden.text
    assert "Личная" in hidden.text
    assert "Показать рабочие" in hidden.text

    shown = await client.get("/", cookies={"show_work": "1"})
    assert shown.status_code == 200
    assert "Рабочая" in shown.text
    assert "Скрыть рабочие" in shown.text


@pytest.mark.asyncio
async def test_dashboard_shows_everything_on_monday(client, db, monkeypatch):
    from app.web.routes import dashboard as dashboard_module

    await _make_tasks(db, due_date=date(2026, 9, 21))

    monkeypatch.setattr(dashboard_module, "date", _Monday)
    resp = await client.get("/")

    assert resp.status_code == 200
    assert "Рабочая" in resp.text
    # Кнопки на буднях нет — скрывать нечего.
    assert "Показать рабочие" not in resp.text
