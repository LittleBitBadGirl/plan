"""tasks.is_archived: пустое значение — это «не в архиве».

Revision ID: 014_task_is_archived
Revises: 013_achievements
Create Date: 2026-09-22

В базе Веры 29 задач (все с source='hermes') лежали с пустым is_archived —
их писала интеграция мимо ORM, и колонка оставалась незаполненной. Любое
сравнение `is_archived == False` в SQL отбрасывает NULL: такие задачи
пропадали из бэклога, дашборда, статистики и счётчиков категорий
(счётчик «Работа» показывал 1 вместо 28, свежая задача в «Пет-проектах»
не появлялась вовсе).

Миграция закрывает причину: заполняет существующие строки значением по
умолчанию и ставит триггер, чтобы колонка заполнялась при ЛЮБОЙ вставке,
включая прямые INSERT от внешних интеграций.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import table_exists

revision: str = "014_task_is_archived"
down_revision: Union[str, None] = "013_achievements"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS tasks_is_archived_default
AFTER INSERT ON tasks
WHEN NEW.is_archived IS NULL
BEGIN
    UPDATE tasks SET is_archived = 0 WHERE id = NEW.id;
END
"""

# Симметричная защита на UPDATE: сырой скрипт интеграции или ручная правка
# могли вернуть NULL уже после заполнения. UPDATE OF is_archived срабатывает
# только когда колонку реально трогают, и не рекурсирует: внутренний UPDATE
# ставит 0, при котором условие WHEN уже ложно.
UPDATE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS tasks_is_archived_default_update
AFTER UPDATE OF is_archived ON tasks
WHEN NEW.is_archived IS NULL
BEGIN
    UPDATE tasks SET is_archived = 0 WHERE id = NEW.id;
END
"""


def upgrade() -> None:
    if not table_exists("tasks"):
        return
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE tasks SET is_archived = 0 WHERE is_archived IS NULL"))
    conn.execute(sa.text(TRIGGER_SQL))
    conn.execute(sa.text(UPDATE_TRIGGER_SQL))


def downgrade() -> None:
    """Убирает защиту колонки. Данные не откатываются: какие строки были NULL
    до миграции, миграция не помнит, а пустое значение и ноль код читает
    одинаково — возвращать NULL смысла нет.
    """
    if not table_exists("tasks"):
        return
    bind = op.get_bind()
    bind.execute(sa.text("DROP TRIGGER IF EXISTS tasks_is_archived_default"))
    bind.execute(sa.text("DROP TRIGGER IF EXISTS tasks_is_archived_default_update"))
