"""Дата из бэклога (⋯ → «Поставить дату»): planned_for, а не срок.

Проверяем поведение через HTTP, как просила Вера 09.10.2026: день «сегодня»
уводит задачу из бэклога в день, будущий день оставляет её в бэклоге с подписью
«на 15.10», пустое поле возвращает задачу в обычный бэклог, а ночной возврат
будущую дату не трогает.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db.database import async_session
from app.models.task import Task
from app.services.day_pool_service import count_backlog, return_unfinished_to_backlog


async def _task(title: str):
    async with async_session() as s:
        return (await s.execute(select(Task).where(Task.title == title))).scalar_one_or_none()


async def _make(client, title: str) -> int:
    """Задача в бэклоге (through the web form — без AI-категоризации)."""
    resp = await client.post("/tasks/web/create", data={"title": title, "where": "backlog"})
    assert resp.status_code in (200, 303), resp.text
    task = await _task(title)
    assert task is not None, "задача не создалась"
    assert task.planned_for is None, "новая задача должна ждать в бэклоге"
    return task.id


async def _count() -> int:
    async with async_session() as s:
        return await count_backlog(s)


@pytest.mark.asyncio
async def test_date_today_moves_task_to_day(client):
    """Дата «сегодня» = та же механика, что кнопка-молния: задача сразу в дне."""
    tid = await _make(client, "Дата сегодня")

    resp = await client.post(
        f"/backlog/{tid}/plan-date", data={"planned_for": date.today().isoformat()}
    )
    assert resp.status_code == 200

    task = await _task("Дата сегодня")
    assert task.planned_for == date.today()
    assert "Дата сегодня" not in resp.text, "задача должна уйти из бэклога"
    dashboard = await client.get("/")
    assert "Дата сегодня" in dashboard.text, "и появиться в задачах дня"


@pytest.mark.asyncio
async def test_future_date_keeps_task_in_backlog_with_badge(client):
    """Будущий день: задача остаётся в бэклоге с подписью, в сегодня не лезет."""
    future = date.today() + timedelta(days=6)
    tid = await _make(client, "Дата в будущем")

    resp = await client.post(
        f"/backlog/{tid}/plan-date", data={"planned_for": future.isoformat()}
    )
    assert resp.status_code == 200

    task = await _task("Дата в будущем")
    assert task.planned_for == future
    assert "Дата в будущем" in resp.text, "задача должна остаться видимой в бэклоге"
    assert f"на {future.strftime('%d.%m')}" in resp.text, "нет подписи с датой"

    dashboard = await client.get("/")
    assert "Дата в будущем" not in dashboard.text, "будущая задача не должна попадать в сегодня"


@pytest.mark.asyncio
async def test_empty_date_returns_task_to_plain_backlog(client):
    """Пустое поле — дата снимается, задача снова обычная в бэклоге."""
    tid = await _make(client, "Снять дату")
    await client.post(
        f"/backlog/{tid}/plan-date",
        data={"planned_for": (date.today() + timedelta(days=3)).isoformat()},
    )

    resp = await client.post(f"/backlog/{tid}/plan-date", data={"planned_for": ""})
    assert resp.status_code == 200
    task = await _task("Снять дату")
    assert task.planned_for is None
    assert "Снять дату" in resp.text


@pytest.mark.asyncio
async def test_counter_agrees_with_list(client):
    """Число «в бэклоге» считает то же, что видно на экране: задача с датой — внутри."""
    tid = await _make(client, "Считаем в бэклоге")
    before = await _count()

    await client.post(
        f"/backlog/{tid}/plan-date",
        data={"planned_for": (date.today() + timedelta(days=2)).isoformat()},
    )
    after = await _count()
    assert after == before, "задача с будущей датой остаётся в бэклоге — число падать не должно"

    page = await client.get("/backlog")
    assert "Считаем в бэклоге" in page.text


@pytest.mark.asyncio
async def test_night_return_leaves_future_date_alone(client):
    """Ночной возврат забирает только прошедшие дни: будущая дата ждёт своего дня."""
    future = date.today() + timedelta(days=5)
    tid = await _make(client, "Ждёт своего дня")
    await client.post(f"/backlog/{tid}/plan-date", data={"planned_for": future.isoformat()})

    async with async_session() as s:
        result = await return_unfinished_to_backlog(s)
        await s.commit()
    assert result["moved"] == 0, "будущая дата не должна сниматься ночным возвратом"

    task = await _task("Ждёт своего дня")
    assert task.planned_for == future


@pytest.mark.asyncio
async def test_bad_date_is_rejected(client):
    """Мусор в поле даты не должен тихо проставить что попало."""
    tid = await _make(client, "Кривая дата")
    resp = await client.post(f"/backlog/{tid}/plan-date", data={"planned_for": "завтра"})
    assert resp.status_code == 400
    task = await _task("Кривая дата")
    assert task.planned_for is None
