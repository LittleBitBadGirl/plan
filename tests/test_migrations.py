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
from app.models import event as _event  # noqa: F401
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
        assert version == "021_event_visit"

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
        event_cols = {
            row[1] for row in sync.execute("PRAGMA table_info(events)")
        }
        event_indexes = {
            row[1] for row in sync.execute("PRAGMA index_list(events)")
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
        assert "events" in tables
        assert {
            "title",
            "description",
            "url",
            "image_url",
            "image_file",
            "location",
            "start_date",
            "end_date",
            "start_time",
            "visit_date",
            "visit_time",
            "status",
            "is_archived",
        } <= event_cols
        assert "ix_events_range" in event_indexes

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

        assert version == "021_event_visit"
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


@pytest.mark.asyncio
async def test_habit_log_migration_replaces_legacy_unique_and_fixes_overlap():
    """Миграция 019: в боевой базе осталось UNIQUE (habit_id, date).

    Из-за него отметка дня, который уже отмечен в прошлом цикле, падала на
    IntegrityError («день не отмечается»): сервер отдавал 500, а дашборд гасил
    ошибку молча. Миграция пересобирает таблицу с ограничением по циклу и
    убирает накопившийся нахлёст в данных.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "habit_migrate.db")
        url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_async_engine(url, connect_args={"check_same_thread": False})

        # Старая таблица — как в боевой базе: колонка цикла есть, ограничение парное.
        sync = sqlite3.connect(db_path)
        sync.execute(
            """
            CREATE TABLE habit_logs (
                id INTEGER NOT NULL,
                habit_id INTEGER NOT NULL,
                date DATE NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                cycle_number INTEGER DEFAULT 1,
                PRIMARY KEY (id),
                CONSTRAINT _habit_date_uc UNIQUE (habit_id, date),
                FOREIGN KEY(habit_id) REFERENCES habits (id) ON DELETE CASCADE
            )
            """
        )
        sync.execute("CREATE INDEX ix_habit_logs_id ON habit_logs (id)")
        sync.commit()
        sync.close()

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        sync = sqlite3.connect(db_path)
        # Привычка Веры: цикл 1 = 30.08–28.09, «След. 30 дней» нажали 28.09 —
        # новый цикл стартовал в тот же день, день попал в оба цикла.
        sync.execute(
            "INSERT INTO habits (id, title, start_date, target_days, current_cycle, is_active, is_archived) "
            "VALUES (8, 'не курю', '2026-09-28', 30, 2, 1, 0)"
        )
        sync.execute(
            "INSERT INTO habit_logs (id, habit_id, cycle_number, date) VALUES (1, 8, 1, '2026-08-30')"
        )
        sync.execute(
            "INSERT INTO habit_logs (id, habit_id, cycle_number, date) VALUES (2, 8, 1, '2026-09-28')"
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
        ddl = sync.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'habit_logs'"
        ).fetchone()[0]
        leftover = sync.execute(
            "SELECT name FROM sqlite_master WHERE name = 'habit_logs_legacy'"
        ).fetchall()
        marks = sync.execute(
            "SELECT id, habit_id, cycle_number, date FROM habit_logs ORDER BY id"
        ).fetchall()
        start_date = sync.execute("SELECT start_date FROM habits WHERE id = 8").fetchone()[0]
        indexes = {row[1] for row in sync.execute("PRAGMA index_list(habit_logs)")}
        version = sync.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        sync.close()

        assert "_habit_date_cycle_uc" in ddl, "ограничение не заменено на «по циклу»"
        assert "_habit_date_uc" not in ddl
        assert leftover == [], "временная таблица осталась в базе"
        assert marks == [
            (1, 8, 1, "2026-08-30"),
            (2, 8, 1, "2026-09-28"),
        ], "отметки потерялись при пересборке таблицы"
        assert indexes >= {"ix_habit_logs_id", "ix_habit_logs_habit_cycle"}
        assert version == "021_event_visit"
        assert start_date == "2026-09-29", "нахлёст не убран: цикл всё ещё начинается днём прошлого"

        # Повторный прогон (контейнер перезапускается) ничего не меняет.
        settings.database_url = url
        try:
            run_migrations()
        finally:
            settings.database_url = prev_url

        sync = sqlite3.connect(db_path)
        after_rerun = sync.execute(
            "SELECT id, habit_id, cycle_number, date FROM habit_logs ORDER BY id"
        ).fetchall()
        start_after_rerun = sync.execute("SELECT start_date FROM habits WHERE id = 8").fetchone()[0]
        sync.close()
        assert after_rerun == marks, "повторный прогон миграции изменил отметки"
        assert start_after_rerun == "2026-09-29", "повторный прогон сдвинул старт ещё раз"

        # Главное: день, отмеченный в прошлом цикле, теперь отмечается и в новом.
        sync = sqlite3.connect(db_path)
        sync.execute(
            "INSERT INTO habit_logs (habit_id, cycle_number, date) VALUES (8, 2, '2026-09-28')"
        )
        sync.commit()
        same_day = sync.execute(
            "SELECT COUNT(*) FROM habit_logs WHERE habit_id = 8 AND date = '2026-09-28'"
        ).fetchone()[0]
        sync.close()
        assert same_day == 2, "день по-прежнему нельзя отметить в двух циклах"

        await engine.dispose()


@pytest.mark.asyncio
async def test_event_visit_migration_clears_decisions_without_a_day():
    """Миграция 021: у похода есть день, а решения без дня больше не держатся.

    «Не иду» из интерфейса убрано. «Иду» без дня сеанса — не бронь: из такой
    строки нельзя ни собрать событие календаря, ни показать «иду 5 окт», поэтому
    её тоже переводим в «без отметки». Бронь с днём миграция не трогает.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "event_visit_migrate.db")
        url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_async_engine(url, connect_args={"check_same_thread": False})

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from app.config import settings

        prev_url = settings.database_url
        settings.database_url = url
        try:
            run_migrations()

            # Откатываем версию до 020 и кладём строки такими, какими их оставил
            # прежний интерфейс: решение «не иду» и «иду» без дня похода.
            sync = sqlite3.connect(db_path)
            sync.execute("UPDATE alembic_version SET version_num = '020_events'")
            sync.execute(
                "INSERT INTO events (title, start_date, status, is_archived) "
                "VALUES ('Не иду', '2026-10-05', 'not_going', 0)"
            )
            sync.execute(
                "INSERT INTO events (title, start_date, status, is_archived) "
                "VALUES ('Иду без дня', '2026-10-05', 'going', 0)"
            )
            sync.execute(
                "INSERT INTO events (title, start_date, visit_date, visit_time, status, is_archived) "
                "VALUES ('Иду 5 окт', '2026-10-05', '2026-10-05', '14:00', 'going', 0)"
            )
            sync.commit()
            sync.close()

            run_migrations()
        finally:
            settings.database_url = prev_url

        sync = sqlite3.connect(db_path)
        statuses = dict(sync.execute("SELECT title, status FROM events").fetchall())
        visits = dict(sync.execute("SELECT title, visit_date FROM events").fetchall())
        version = sync.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        cols = {row[1] for row in sync.execute("PRAGMA table_info(events)")}
        sync.close()

        assert version == "021_event_visit"
        assert {"visit_date", "visit_time"} <= cols
        assert statuses["Не иду"] == "none"
        assert statuses["Иду без дня"] == "none"
        assert statuses["Иду 5 окт"] == "going", "бронь с днём миграция не трогает"
        assert visits["Иду 5 окт"] == "2026-10-05"

        # Повторный прогон (контейнер перезапускается) ничего не ломает.
        settings.database_url = url
        try:
            run_migrations()
        finally:
            settings.database_url = prev_url

        sync = sqlite3.connect(db_path)
        again = dict(sync.execute("SELECT title, status FROM events").fetchall())
        sync.close()
        assert again == statuses, "повторный прогон миграции изменил решения"

        await engine.dispose()
