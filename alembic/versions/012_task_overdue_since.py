"""Якорь просрочки: tasks.overdue_since.

Revision ID: 012_task_overdue_since
Revises: 011_offline_sync
Create Date: 2026-09-18

Зачем: счётчик `postpones` сбрасывался при ручном переносе задачи на будущую
дату и считался только по рабочим дням, поэтому бейдж показывал «×2» у задачи,
которая висит больше недели. `overdue_since` хранит день, когда задачу должны
были сделать, и от него бейдж считается напрямую — без сбросов и без зависимости
от того, отработал ли ночной перенос.

Идемпотентна: `init_db` делает `create_all` до миграций, поэтому колонка на
чистой базе может уже существовать.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "012_task_overdue_since"
down_revision: Union[str, None] = "011_offline_sync"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    rows = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def upgrade() -> None:
    if not _column_exists("tasks", "overdue_since"):
        with op.batch_alter_table("tasks") as batch:
            batch.add_column(sa.Column("overdue_since", sa.Date(), nullable=True))

    # Бэкфилл по старым задачам. Точную историю восстановить нельзя (прежний
    # счётчик терял значения), поэтому берём лучшее из доступного:
    #  • задача висит прямо сейчас → якорь = её дата, то есть реальный срок просрочки;
    #  • задача только что «уехала» на сегодня ночным переносом → якорь = дата минус
    #    прежние переносы, чтобы число не стало меньше того, что уже показывалось.
    op.execute(
        sa.text(
            """
            UPDATE tasks
               SET overdue_since = due_date
             WHERE overdue_since IS NULL
               AND due_date IS NOT NULL
               AND due_date < date('now')
               AND is_archived = 0
               AND status IN ('новая', 'в_работе')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE tasks
               SET overdue_since = date(due_date, '-' || postpones || ' days')
             WHERE overdue_since IS NULL
               AND due_date IS NOT NULL
               AND due_date <= date('now')
               AND COALESCE(postpones, 0) > 0
               AND is_archived = 0
               AND status IN ('новая', 'в_работе')
            """
        )
    )


def downgrade() -> None:
    if _column_exists("tasks", "overdue_since"):
        with op.batch_alter_table("tasks") as batch:
            batch.drop_column("overdue_since")
