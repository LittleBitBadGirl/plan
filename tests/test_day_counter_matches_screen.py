"""Счётчик дня обязан совпадать с тем, что видно на экране.

Вера 28.10.2026: «когда я с дашборда убираю обратно в бэклог задачи, счётчик не
уменьшается: убрала 1 и теперь некорректное отображение — осталось 2 задачи, а
на счётчике надо ещё 3 закрыть».

Причин было две, и обе проверяются здесь:

1. Задача со статусом, которого нет в словаре приложения (`open` — так его
   записала интеграция Hermes мимо ORM), попадала в счётчики «на день» и
   «прогресс», но не проходила фильтр списка дня: на экране её не было, а
   счётчик её считал.
2. Взятые на день подзадачи не входили в полоску «Прогресс» вовсе. Вера берёт в
   день куски больших задач, поэтому возврат подзадачи убирал карточку, но не
   число: «на день» падало, а прогресс оставался прежним.
"""
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.database import async_session
from app.models.task import STATUS_ALIASES, Task, task_is_open
from app.web.deps import (
    build_day_pool_context,
    get_dashboard_day_stats,
    get_today_actionable_stats,
    get_today_progress,
)


async def _fresh(fn):
    """Прочитать число заново из базы, а не из кеша сессии."""
    async with async_session() as s:
        return await fn(s)


@pytest.mark.asyncio
async def test_foreign_status_is_visible_in_day(client, db):
    """Статус `open` из интеграции не делает задачу невидимой на дашборде."""
    today = date.today()
    db.add(Task(
        title="Психолог: ревность — отдать письмо",
        status="open",
        source="hermes",
        planned_for=today,
        item_kind="task",
    ))
    await db.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Психолог: ревность — отдать письмо" in resp.text, (
        "задача со статусом open не попала в список дня, хотя счётчик её считает"
    )


@pytest.mark.asyncio
async def test_counter_equals_visible_cards(db):
    """Числа сводки считаются по тем же задачам, что видит список дня."""
    today = date.today()
    db.add(Task(title="Открытая с чужим статусом", status="open", source="hermes",
                planned_for=today, item_kind="task"))
    db.add(Task(title="Открытая обычная", status="новая", source="web",
                planned_for=today, item_kind="task"))
    await db.commit()

    bundle = await _fresh(get_dashboard_day_stats)
    open_rows = await _fresh(
        lambda s: s.execute(
            select(Task).where(
                Task.planned_for == today,
                task_is_open(),
                Task.parent_task_id.is_(None),
                Task.item_kind == "task",
            )
        )
    )
    open_titles = [t.title for t in open_rows.scalars().all()]

    assert bundle.total == 2, "обе открытые задачи дня обязаны быть в знаменателе"
    assert bundle.completed == 0
    assert len(open_titles) == bundle.total - bundle.completed, (
        "«закрыть ещё N» не совпадает с числом открытых карточек дня"
    )


@pytest.mark.asyncio
async def test_returning_subtask_decrements_day_progress(client, db):
    """Возврат взятой подзадачи в бэклог уменьшает прогресс на одну карточку."""
    today = date.today()
    done_root = Task(
        title="Закрытая корневая",
        planned_for=today,
        status="выполнена",
        completed_at=datetime.utcnow(),
        is_archived=True,
        source="web",
        item_kind="task",
    )
    parent = Task(title="Большая задача", status="новая", source="web", item_kind="task")
    db.add_all([done_root, parent])
    await db.flush()
    sub1 = Task(title="Кусок 1", parent_task_id=parent.id, status="новая",
                source="web", item_kind="task", planned_for=today)
    sub2 = Task(title="Кусок 2", parent_task_id=parent.id, status="новая",
                source="web", item_kind="task", planned_for=today)
    db.add_all([sub1, sub2])
    await db.commit()
    sub_id = sub1.id

    assert await _fresh(get_today_progress) == (1, 3)

    resp = await client.post(f"/tasks/{sub_id}/backlog")
    assert resp.status_code == 200

    assert await _fresh(get_today_progress) == (1, 2), (
        "после возврата подзадачи в бэклог знаменатель прогресса не уменьшился"
    )

    pool = await _fresh(lambda s: build_day_pool_context(s, None, today))
    assert pool["taken"] == 2, "«на день» и «прогресс» считают одно и то же"


@pytest.mark.asyncio
async def test_returning_root_task_decrements_day_progress(client, db):
    """Возврат корневой задачи уносит её из прогресса вместе с карточкой."""
    today = date.today()
    root = Task(title="Корневая на день", planned_for=today, status="новая",
                source="web", item_kind="task")
    db.add(root)
    await db.commit()
    root_id = root.id

    assert await _fresh(get_today_progress) == (0, 1)
    resp = await client.post(f"/tasks/{root_id}/backlog")
    assert resp.status_code == 200
    assert await _fresh(get_today_progress) == (0, 0)


@pytest.mark.asyncio
async def test_api_normalizes_foreign_status(client, db):
    """PUT /api/tasks приводит чужое слово статуса к словарю приложения."""
    task = Task(title="Из интеграции", status="новая", source="hermes", item_kind="task")
    db.add(task)
    await db.commit()
    task_id = task.id

    resp = await client.put(f"/api/tasks/{task_id}", json={"status": "open"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "новая"

    stored = await _fresh(
        lambda s: s.execute(select(Task.status).where(Task.id == task_id))
    )
    assert stored.scalar_one() == "новая"


@pytest.mark.asyncio
async def test_banner_does_not_double_count_taken_subtask(db):
    """Родитель и его подзадача оба в дне: нагрузка считается один раз.

    Баннер «Сегодня N задач» складывал карточки дня и подзадачи родителей и
    считал взятую подзадачу дважды — это замечание критика к первому варианту
    фикса (регрессия, а не старое поведение).
    """
    today = date.today()
    parent = Task(title="Ремонт", status="новая", source="web",
                  item_kind="task", planned_for=today)
    db.add(parent)
    await db.flush()
    db.add(Task(title="Купить краску", parent_task_id=parent.id, status="новая",
                source="web", item_kind="task", planned_for=today))
    await db.commit()

    completed, total = await _fresh(get_today_actionable_stats)
    assert (completed, total) == (0, 1), (
        "взятая подзадача посчитана и карточкой дня, и подзадачей родителя"
    )


@pytest.mark.asyncio
async def test_postponed_root_is_not_in_day_progress(client, db):
    """«Отложена» не висит в знаменателе прогресса и не показывается в дне."""
    today = date.today()
    db.add(Task(title="Отложенная на сегодня", status="отложена", source="web",
                item_kind="task", planned_for=today))
    await db.commit()

    assert await _fresh(get_today_progress) == (0, 0)

    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Отложенная на сегодня" not in resp.text


@pytest.mark.asyncio
async def test_api_put_done_closes_task_fully(client, db):
    """PUT со статусом done закрывает задачу целиком: статус, дата, архив."""
    today = date.today()
    task = Task(title="Закрыть через API", status="новая", source="hermes",
                item_kind="task", planned_for=today)
    db.add(task)
    await db.commit()
    task_id = task.id

    resp = await client.put(f"/api/tasks/{task_id}", json={"status": "done"})
    assert resp.status_code == 200

    async with async_session() as s:
        stored = (await s.execute(select(Task).where(Task.id == task_id))).scalar_one()
    assert stored.status == "выполнена"
    assert stored.completed_at is not None, "выполнена без даты закрытия — невидимая задача дня"
    assert stored.is_archived is True

    # И прогресс считает её закрытой, а не висящей в остатке
    assert await _fresh(get_today_progress) == (1, 1)


def test_migration_aliases_match_model():
    """Таблица алиасов в миграции не разъехалась со словарём модели."""
    migration = Path(__file__).resolve().parent.parent / "alembic" / "versions" / (
        "030_normalize_task_status.py"
    )
    text = migration.read_text(encoding="utf-8")
    for source, target in STATUS_ALIASES.items():
        if source == target:
            continue  # в миграции только чужие слова: канонические не трогаем
        assert f'"{source}": "{target}"' in text, (
            f"миграция 030 потеряла алиас {source} → {target}"
        )

