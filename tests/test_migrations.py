import os
import sqlite3
import tempfile

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.migrate import run_migrations
from app.models import calendar_event as _ce  # noqa: F401
from app.models import calendar_ignore_rule as _cir  # noqa: F401
from app.models import investment as _inv  # noqa: F401
from app.models import manager as _mgr  # noqa: F401
from app.models import offline as _offl  # noqa: F401
from app.models import period_entry as _pe  # noqa: F401
from app.models import portfolio as _pf  # noqa: F401
from app.models import recurring_completion as _rc  # noqa: F401
from app.models.base import Base


@pytest.mark.asyncio
async def test_run_migrations_on_fresh_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "migrate_test.db")
        url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_async_engine(url, connect_args={"check_same_thread": False})
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from app.config import settings

        prev_url = settings.database_url
        settings.database_url = url
        try:
            run_migrations()
        finally:
            settings.database_url = prev_url

        async with session_factory() as session:
            result = await session.execute(
                text("SELECT version_num FROM alembic_version")
            )
            version = result.scalar_one()
        assert version == "011_offline_sync"

        sync = sqlite3.connect(db_path)
        task_cols = {row[1] for row in sync.execute("PRAGMA table_info(tasks)")}
        flow_cols = {row[1] for row in sync.execute("PRAGMA table_info(investment_flows)")}
        tables = {
            row[0]
            for row in sync.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        task_indexes = {
            row[1]
            for row in sync.execute("PRAGMA index_list(tasks)")
        }
        habit_indexes = {
            row[1]
            for row in sync.execute("PRAGMA index_list(habit_logs)")
        }
        feedback_cols = {
            row[1] for row in sync.execute("PRAGMA table_info(manager_feedback)")
        }
        feedback_indexes = {
            row[1]
            for row in sync.execute("PRAGMA index_list(manager_feedback)")
        }
        seeded_managers = {
            row[0] for row in sync.execute("SELECT name FROM managers")
        }
        action_indexes = {
            row[1] for row in sync.execute("PRAGMA index_list(offline_actions)")
        }
        conflict_indexes = {
            row[1] for row in sync.execute("PRAGMA index_list(offline_conflicts)")
        }
        action_cols = {
            row[1] for row in sync.execute("PRAGMA table_info(offline_actions)")
        }
        sync.close()
        assert "estimated_minutes" in task_cols
        assert "portfolio_id" in flow_cols
        assert "portfolios" in tables
        assert "managers" in tables
        assert "manager_feedback" in tables
        assert "ix_tasks_dashboard_day" in task_indexes
        assert "ix_tasks_completed_at" in task_indexes
        assert "ix_habit_logs_habit_cycle" in habit_indexes
        assert "ix_manager_feedback_manager" in feedback_indexes
        assert {"period_month", "kind", "files", "links"} <= feedback_cols
        assert "Алёна Савченко" in seeded_managers
        assert "offline_actions" in tables
        assert "offline_conflicts" in tables
        assert "ix_offline_actions_client_uuid" in action_indexes
        assert "ix_offline_conflicts_task_id" in conflict_indexes
        assert {"client_uuid", "kind", "task_id", "status"} <= action_cols

        await engine.dispose()
