"""Офлайн-синхронизация: идемпотентность, слияние полей, конфликты.

Ключевое требование Веры — «не перетирало, а синхронизировалось». Поэтому
проверяется не только применение правки, но и то, что серверное изменение,
сделанное за время отсутствия, выживает.
"""
from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.offline import OfflineConflict
from app.models.task import Task


@pytest_asyncio.fixture(autouse=True)
async def clean_offline_tables():
    """Журнал действий и конфликты не должны переезжать между тестами."""
    from sqlalchemy import delete

    from app.db.database import async_session
    from app.models.offline import OfflineAction

    async with async_session() as session:
        await session.execute(delete(OfflineConflict))
        await session.execute(delete(OfflineAction))
        await session.commit()
    yield


async def _sync(client, actions):
    resp = await client.post("/api/offline/sync", json={"actions": actions})
    assert resp.status_code == 200
    return resp.json()


async def _create(client, uuid="create-1", title="Задача с телефона"):
    data = await _sync(
        client,
        [{"client_uuid": uuid, "kind": "create_task", "payload": {"title": title}}],
    )
    result = data["results"][0]
    assert result["status"] == "applied", result
    return result["task_id"]


@pytest.mark.asyncio
async def test_create_task_from_offline(client, db):
    task_id = await _create(client, title="Купить молоко")

    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
    assert task.title == "Купить молоко"
    assert task.source == "offline"
    assert task.due_date == date.today()


@pytest.mark.asyncio
async def test_repeated_send_creates_single_task(client, db):
    """Повторная досылка того же действия не должна плодить дубли."""
    first = await _create(client, uuid="dup-1", title="Одна задача")
    data = await _sync(
        client,
        [{"client_uuid": "dup-1", "kind": "create_task", "payload": {"title": "Одна задача"}}],
    )

    assert data["results"][0]["status"] == "duplicate"
    assert data["results"][0]["task_id"] == first

    tasks = (await db.execute(select(Task).where(Task.title == "Одна задача"))).scalars().all()
    assert len(tasks) == 1


@pytest.mark.asyncio
async def test_offline_change_applies_when_other_field_changed_on_server(client, db):
    """Главный сценарий «не перетрёт»: правили разные поля — выживают оба."""
    task_id = await _create(client, uuid="mix-1", title="Отчёт")

    # Вера на компьютере перенесла дату, пока телефон был без сети.
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
    server_due = date.today() + timedelta(days=5)
    task.due_date = server_due
    await db.commit()

    data = await _sync(
        client,
        [
            {
                "client_uuid": "mix-2",
                "kind": "update_fields",
                "task_id": task_id,
                "payload": {
                    "changes": [{"field": "title", "from": "Отчёт", "to": "Отчёт за сентябрь"}]
                },
            }
        ],
    )

    assert data["results"][0]["status"] == "applied"
    # Сессия теста кэширует объект: без expire_all() вернётся старое название.
    db.expire_all()
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
    assert task.title == "Отчёт за сентябрь"
    assert task.due_date == server_due, "серверный перенос не должен потеряться"


@pytest.mark.asyncio
async def test_same_field_changed_both_sides_becomes_conflict(client, db):
    """Одно поле правили и офлайн, и на сервере — сервер не угадывает."""
    task_id = await _create(client, uuid="conf-1", title="Позвонить")

    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
    server_due = date.today() + timedelta(days=3)
    task.due_date = server_due
    await db.commit()

    local_due = date.today() + timedelta(days=10)
    data = await _sync(
        client,
        [
            {
                "client_uuid": "conf-2",
                "kind": "update_fields",
                "task_id": task_id,
                "payload": {
                    "changes": [
                        {
                            "field": "due_date",
                            "from": date.today().isoformat(),
                            "to": local_due.isoformat(),
                        }
                    ]
                },
            }
        ],
    )

    assert data["results"][0]["status"] == "conflict"
    assert data["conflicts_total"] == 1

    await db.commit()
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
    assert task.due_date == server_due, "конфликт не должен затирать серверное значение"

    conflicts = (await db.execute(select(OfflineConflict))).scalars().all()
    assert len(conflicts) == 1
    assert conflicts[0].field == "due_date"
    assert conflicts[0].local_value == local_due.isoformat()
    assert conflicts[0].server_value == server_due.isoformat()


@pytest.mark.asyncio
async def test_conflict_resolution_keep_local(client, db):
    """Вера выбрала своё значение — оно и применяется."""
    task_id = await _create(client, uuid="res-1", title="Задача с конфликтом")

    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
    task.due_date = date.today() + timedelta(days=3)
    await db.commit()

    local_due = date.today() + timedelta(days=10)
    await _sync(
        client,
        [
            {
                "client_uuid": "res-2",
                "kind": "update_fields",
                "task_id": task_id,
                "payload": {
                    "changes": [
                        {
                            "field": "due_date",
                            "from": date.today().isoformat(),
                            "to": local_due.isoformat(),
                        }
                    ]
                },
            }
        ],
    )

    listed = (await client.get("/api/offline/conflicts")).json()["conflicts"]
    assert len(listed) == 1

    resolved = await client.post(
        f"/api/offline/conflicts/{listed[0]['id']}/resolve",
        json={"resolution": "keep_local"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["ok"] is True

    await db.commit()
    db.expire_all()
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
    assert task.due_date == local_due
    assert (await client.get("/api/offline/conflicts")).json()["conflicts"] == []


@pytest.mark.asyncio
async def test_complete_archives_task(client, db):
    task_id = await _create(client, uuid="done-1", title="Закрыть задачу")

    data = await _sync(
        client, [{"client_uuid": "done-2", "kind": "complete", "task_id": task_id}]
    )

    assert data["results"][0]["status"] == "applied"
    await db.commit()
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
    assert task.status == "выполнена"
    assert task.is_archived is True


@pytest.mark.asyncio
async def test_complete_twice_is_duplicate_not_error(client, db):
    task_id = await _create(client, uuid="twice-1", title="Двойное закрытие")
    await _sync(client, [{"client_uuid": "twice-2", "kind": "complete", "task_id": task_id}])
    data = await _sync(
        client, [{"client_uuid": "twice-3", "kind": "complete", "task_id": task_id}]
    )

    assert data["results"][0]["status"] in ("duplicate", "applied")


@pytest.mark.asyncio
async def test_to_backlog_removes_date(client, db):
    task_id = await _create(client, uuid="bl-1", title="В бэклог")

    data = await _sync(client, [{"client_uuid": "bl-2", "kind": "to_backlog", "task_id": task_id}])

    assert data["results"][0]["status"] == "applied"
    await db.commit()
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
    assert task.due_date is None
    assert task.postpones == 0


@pytest.mark.asyncio
async def test_unknown_action_rejected_without_500(client):
    data = await _sync(
        client, [{"client_uuid": "bad-1", "kind": "что_то_такое", "task_id": 1}]
    )

    assert data["results"][0]["status"] in ("rejected", "error")


@pytest.mark.asyncio
async def test_broken_action_does_not_block_the_rest(client, db):
    """Одно битое действие не должно отменять остальные из пачки."""
    good_id = await _create(client, uuid="mixok-1", title="Хорошая")

    data = await _sync(
        client,
        [
            {"client_uuid": "bad-2", "kind": "update_fields", "task_id": 999999, "payload": {}},
            {"client_uuid": "good-2", "kind": "to_backlog", "task_id": good_id},
        ],
    )

    statuses = [result["status"] for result in data["results"]]
    assert statuses[0] == "rejected"
    assert statuses[1] == "applied"
