import os
import sqlite3
import tempfile

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.migrate import run_migrations
from app.models import achievement as _ach  # noqa: F401
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
        assert version == "018_reading_format_document"

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
        achievement_cols = {
            row[1] for row in sync.execute("PRAGMA table_info(achievements)")
        }
        achievement_indexes = {
            row[1] for row in sync.execute("PRAGMA index_list(achievements)")
        }
        sync.close()
        assert "estimated_minutes" in task_cols
        assert "overdue_since" in task_cols
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
        assert "achievements" in tables
        assert {"text", "sphere", "tag", "happened_on", "source", "is_archived"} <= achievement_cols
        assert "ix_achievements_sphere" in achievement_indexes

        await engine.dispose()


@pytest.mark.asyncio
async def test_achievements_migration_creates_table_itself():
    """Таблицу достижений создаёт сама миграция 013, а не только create_all."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "ach_migrate.db")
        url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_async_engine(url, connect_args={"check_same_thread": False})

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from app.config import settings

        prev_url = settings.database_url
        settings.database_url = url
        try:
            run_migrations()

            # откатываем состояние до 012 и убираем таблицу, как будто база старая
            sync = sqlite3.connect(db_path)
            sync.execute("DROP TABLE achievements")
            sync.execute("UPDATE alembic_version SET version_num = '012_task_overdue_since'")
            sync.commit()
            sync.close()

            run_migrations()
        finally:
            settings.database_url = prev_url

        sync = sqlite3.connect(db_path)
        tables = {
            row[0] for row in sync.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        version = sync.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        cols = {row[1] for row in sync.execute("PRAGMA table_info(achievements)")}
        sync.close()

        assert version == "018_reading_format_document"
        assert "achievements" in tables
        assert {"text", "sphere", "is_archived", "created_at"} <= cols

        await engine.dispose()


@pytest.mark.asyncio
async def test_is_archived_migration_fills_empty_values_and_guards_new_rows():
    """Задача с пустым is_archived перестаёт быть невидимой.

    Миграция 014: заполняет существующие пустые значения и ставит триггер,
    чтобы колонка заполнялась и при вставке в обход ORM (source='hermes').
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "archived_migrate.db")
        url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_async_engine(url, connect_args={"check_same_thread": False})

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # задача «из интеграции»: колонка не заполнена (так писались 29 задач в базе Веры)
        sync = sqlite3.connect(db_path)
        sync.execute(
            "INSERT INTO tasks (title, source, is_archived, item_kind) "
            "VALUES ('задача из интеграции', 'hermes', NULL, 'task')"
        )
        sync.commit()
        before = sync.execute("SELECT COUNT(*) FROM tasks WHERE is_archived IS NULL").fetchone()[0]
        sync.close()

        from app.config import settings

        prev_url = settings.database_url
        settings.database_url = url
        try:
            run_migrations()
        finally:
            settings.database_url = prev_url

        sync = sqlite3.connect(db_path)
        after = sync.execute("SELECT COUNT(*) FROM tasks WHERE is_archived IS NULL").fetchone()[0]
        filled = sync.execute(
            "SELECT is_archived FROM tasks WHERE title = 'задача из интеграции'"
        ).fetchone()[0]
        # вставка без колонки — как её делает внешний писатель
        sync.execute(
            "INSERT INTO tasks (title, source, item_kind) VALUES ('новая из интеграции', 'hermes', 'task')"
        )
        sync.commit()
        fresh = sync.execute(
            "SELECT is_archived FROM tasks WHERE title = 'новая из интеграции'"
        ).fetchone()[0]
        triggers = {
            row[0]
            for row in sync.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        }
        sync.close()

        assert before == 1
        assert after == 0, "пустые значения должны быть заполнены миграцией"
        assert filled == 0
        assert fresh == 0, "новая задача из интеграции не должна остаться с пустым is_archived"
        assert "tasks_is_archived_default" in triggers
        assert "tasks_is_archived_default_update" in triggers

        # Сырой UPDATE до NULL — так же возвращал невидимые задачи: защита и на обновление
        sync = sqlite3.connect(db_path)
        sync.execute("UPDATE tasks SET is_archived = NULL WHERE title = 'задача из интеграции'")
        sync.commit()
        nulls_after_update = sync.execute(
            "SELECT COUNT(*) FROM tasks WHERE is_archived IS NULL"
        ).fetchone()[0]
        sync.close()
        assert nulls_after_update == 0, "UPDATE до NULL не должен оставлять невидимых задач"

        await engine.dispose()


@pytest.mark.asyncio
async def test_reading_format_migration_renames_old_pdf_value():
    """Миграция 018: «разбор PDF» → «документ» у записей чтения.

    Трогает только чтение: формат у не-чтения не должен измениться.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "reading_format_migrate.db")
        url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_async_engine(url, connect_args={"check_same_thread": False})

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        sync = sqlite3.connect(db_path)
        sync.execute(
            "INSERT INTO shopping_items (title, item_kind, reading_format, reading_status) "
            "VALUES ('PDF-разбор', 'reading', 'разбор PDF', 'want_to_read')"
        )
        sync.execute(
            "INSERT INTO shopping_items (title, item_kind, reading_format, reading_status) "
            "VALUES ('PDF-статья', 'reading', 'статья', 'want_to_read')"
        )
        sync.execute(
            "INSERT INTO shopping_items (title, item_kind, reading_format, reading_status) "
            "VALUES ('не чтение', 'shopping', 'разбор PDF', 'want_to_read')"
        )
        sync.commit()
        sync.close()

        from app.config import settings

        prev_url = settings.database_url
        settings.database_url = url
        try:
            run_migrations()
        finally:
            settings.database_url = prev_url

        sync = sqlite3.connect(db_path)
        renamed = sync.execute(
            "SELECT reading_format FROM shopping_items WHERE title = 'PDF-разбор'"
        ).fetchone()[0]
        untouched = sync.execute(
            "SELECT reading_format FROM shopping_items WHERE title = 'PDF-статья'"
        ).fetchone()[0]
        other_kind = sync.execute(
            "SELECT reading_format FROM shopping_items WHERE title = 'не чтение'"
        ).fetchone()[0]
        sync.close()

        assert renamed == "документ"
        assert untouched == "статья"
        assert other_kind == "разбор PDF", "формат не-чтения миграция не трогает"

        # Повторный прогон (контейнер перезапускается) не должен ничего менять:
        # в том числе «документ» не должен уехать обратно или в NULL.
        settings.database_url = url
        try:
            run_migrations()
        finally:
            settings.database_url = prev_url

        sync = sqlite3.connect(db_path)
        after_rerun = dict(
            sync.execute("SELECT title, reading_format FROM shopping_items").fetchall()
        )
        sync.close()
        assert after_rerun == {
            "PDF-разбор": "документ",
            "PDF-статья": "статья",
            "не чтение": "разбор PDF",
        }, "повторный прогон миграции изменил данные"

        await engine.dispose()
