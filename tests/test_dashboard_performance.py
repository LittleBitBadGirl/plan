"""Perf/regression tests for dashboard hot path."""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models.task import Task
from app.web.deps import (
    _today_roots_with_sub_completions,
    append_today_stats_oob,
    get_dashboard_day_stats,
)


@pytest.mark.asyncio
async def test_dashboard_html_does_not_auto_sync_calendar(client):
    """На load дашборда не должно быть hx-trigger load → calendar sync."""
    called = {"sync": False}

    async def fake_refresh(*args, **kwargs):
        called["sync"] = True
        return {"upserted": 0}

    with patch(
        "app.services.calendar_sync_service.refresh_calendar_events",
        side_effect=fake_refresh,
    ):
        resp = await client.get("/")

    assert resp.status_code == 200
    assert 'hx-trigger="load' not in resp.text
    assert called["sync"] is False


@pytest.mark.asyncio
async def test_dashboard_calendar_sync_only_via_manual_button(client):
    """Sync endpoint вызывается только явным POST, не GET /."""
    mock_refresh = AsyncMock(return_value={"fetched": 0, "upserted": 0})

    with patch("app.web.routes.calendar.refresh_calendar_events", mock_refresh):
        await client.get("/")
        assert mock_refresh.await_count == 0

        resp = await client.post("/api/calendar/sync")
        assert resp.status_code == 200
        assert mock_refresh.await_count == 1


@pytest.mark.asyncio
async def test_today_roots_ignores_old_completed_subtasks(db):
    """Родитель не подтягивается из подзадачи, закрытой месяц назад."""
    today = date.today()
    old = datetime.now(timezone.utc) - timedelta(days=30)

    parent = Task(title="Old parent", due_date=today - timedelta(days=30), status="новая", source="web")
    db.add(parent)
    await db.flush()

    db.add(Task(
        title="Old sub",
        parent_task_id=parent.id,
        status="выполнена",
        completed_at=old,
        source="web",
    ))
    for i in range(50):
        p = Task(title=f"P{i}", due_date=today - timedelta(days=60), status="новая", source="web")
        db.add(p)
        await db.flush()
        db.add(Task(
            title=f"S{i}",
            parent_task_id=p.id,
            status="выполнена",
            completed_at=old,
            source="web",
        ))
    await db.commit()

    roots = await _today_roots_with_sub_completions(db, today)
    root_ids = {r.id for r in roots}
    assert parent.id not in root_ids


@pytest.mark.asyncio
async def test_today_roots_includes_sub_completed_today(db):
    """Родитель подтягивается, если подзадача закрыта сегодня."""
    today = date.today()
    parent = Task(title="Parent", due_date=today, status="новая", source="web")
    db.add(parent)
    await db.flush()
    db.add(Task(
        title="Sub today",
        parent_task_id=parent.id,
        status="выполнена",
        completed_at=datetime.now(timezone.utc),
        source="web",
    ))
    await db.commit()

    roots = await _today_roots_with_sub_completions(db, today)
    assert parent.id in {r.id for r in roots}


@pytest.mark.asyncio
async def test_dashboard_day_stats_loads_recurring_once(db):
    """Bundle stats — один запрос recurring templates за проход."""
    today = date.today()
    parent = Task(title="Bundle parent", due_date=today, status="новая", source="web")
    db.add(parent)
    await db.flush()
    for i in range(3):
        db.add(Task(title=f"Sub {i}", parent_task_id=parent.id, status="новая", source="web"))
    await db.commit()

    with patch(
        "app.services.recurring_schedule.load_active_recurring_templates",
        wraps=__import__(
            "app.services.recurring_schedule", fromlist=["load_active_recurring_templates"]
        ).load_active_recurring_templates,
    ) as mock_load:
        bundle = await get_dashboard_day_stats(db, today)
        assert mock_load.await_count == 1
        assert bundle.subtask_progress["parent_total"] == 1
        assert bundle.subtask_progress["subtask_total"] == 3


@pytest.mark.asyncio
async def test_append_today_stats_oob_uses_single_bundle(db):
    """HTMX OOB — один bundle вместо каскада stats-вызовов."""
    today = date.today()
    db.add(Task(title="Leaf", due_date=today, status="новая", source="web"))
    await db.commit()

    with patch("app.web.deps.get_dashboard_day_stats", wraps=get_dashboard_day_stats) as mock_bundle:
        html = await append_today_stats_oob("", db)
        assert mock_bundle.await_count == 1
        assert 'id="today-stats-counter"' in html
        assert 'id="today-subtask-stats-block"' in html
        assert 'id="ai-warning-block"' in html
