"""Тесты баннера дневной нагрузки и HTMX OOB после закрытия подзадач."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.task import Task
from app.web.deps import (
    append_today_stats_oob,
    build_daily_load_warning,
    get_today_actionable_stats,
)

pytestmark = pytest.mark.asyncio


async def _seed_overdue_subtask_parent(db, *, sub_count: int = 10, overdue_open: int = 9):
    """Родитель на сегодня с просроченными подзадачами (для баннера > 8)."""
    today = date.today()
    yesterday = today - timedelta(days=1)
    parent = Task(title="Крупный проект", due_date=today, status="новая", source="web")
    db.add(parent)
    await db.flush()

    for i in range(sub_count):
        db.add(
            Task(
                title=f"Шаг {i}",
                parent_task_id=parent.id,
                deadline=yesterday,
                status="новая" if i < overdue_open else "выполнена",
                completed_at=datetime.now(timezone.utc) if i >= overdue_open else None,
                source="web",
            )
        )
    await db.commit()
    return parent


class TestTodayLoadBannerStats:
    async def test_build_daily_load_warning_counts_overdue_subs(self, db):
        await _seed_overdue_subtask_parent(db, sub_count=10, overdue_open=10)

        warning = await build_daily_load_warning(db)
        assert warning is not None
        assert "10 задач" in warning
        assert "0 готово" in warning
        assert "10 осталось" in warning

    async def test_build_daily_load_warning_after_overdue_sub_completed(self, db):
        await _seed_overdue_subtask_parent(db, sub_count=10, overdue_open=9)

        warning = await build_daily_load_warning(db)
        assert warning is not None
        assert "10 задач" in warning
        assert "1 готово" in warning
        assert "9 осталось" in warning

    async def test_actionable_stats_increments_on_complete_flow(self, db):
        today = date.today()
        yesterday = today - timedelta(days=1)
        parent = Task(title="Проект", due_date=today, status="новая", source="web")
        db.add(parent)
        await db.flush()

        open_sub = Task(
            title="Просроченная",
            parent_task_id=parent.id,
            deadline=yesterday,
            status="новая",
            source="web",
        )
        db.add(open_sub)
        for i in range(8):
            db.add(
                Task(
                    title=f"Extra {i}",
                    parent_task_id=parent.id,
                    deadline=yesterday,
                    status="новая",
                    source="web",
                )
            )
        await db.commit()

        before_completed, before_total = await get_today_actionable_stats(db)
        assert before_total == 9
        assert before_completed == 0

        open_sub.status = "выполнена"
        open_sub.completed_at = datetime.now(timezone.utc)
        await db.commit()

        after_completed, after_total = await get_today_actionable_stats(db)
        assert after_total == before_total
        assert after_completed == before_completed + 1


class TestTodayLoadBannerHTMX:
    async def test_complete_subtask_updates_ai_warning_oob(self, client, db):
        await _seed_overdue_subtask_parent(db, sub_count=10, overdue_open=10)

        subs_result = await db.execute(
            select(Task.id).where(
                Task.parent_task_id.isnot(None),
                Task.status == "новая",
            )
        )
        sub_id = subs_result.scalars().first()

        resp = await client.post(f"/tasks/{sub_id}/complete-subtask")
        assert resp.status_code == 200
        assert 'id="ai-warning-block"' in resp.text
        assert 'hx-swap-oob="true"' in resp.text
        assert "1 готово" in resp.text
        assert "9 осталось" in resp.text
        assert "line-through" in resp.text

    async def test_today_stats_endpoint_reflects_actionable_banner(self, client, db):
        await _seed_overdue_subtask_parent(db, sub_count=10, overdue_open=9)

        resp = await client.get("/dashboard/today-stats")
        assert resp.status_code == 200
        assert "1 готово" in resp.text
        assert "9 осталось" in resp.text

    async def test_append_today_stats_oob_includes_subtask_block_id(self, db):
        await _seed_overdue_subtask_parent(db, sub_count=3, overdue_open=3)

        html = await append_today_stats_oob("", db)
        assert 'id="today-subtask-stats-block"' in html
        assert 'id="today-subtask-counter"' in html
