"""День без хвоста: таблица памяти о закрытых днях.

Revision ID: 025_day_wins
Revises: 024_habit_cycle_mode
Create Date: 2026-10-08

Вера: «если день был и все задачи ушли — писать, что ты молодец, мы запомним
этот день». Запомнить день можно только в момент, когда он закрыт: ночной
возврат стирает `planned_for` у незакрытых задач, поэтому задним числом любой
прошлый день выглядит закрытым (на дне остались одни выполненные).

Строка на день: `level` = `tasks` (закрыты все задачи дня) или `full` (ещё и все
регулярные), плюс числа закрытого для справки. Схема не бэкфиллится: памяти о
прошлых днях нет, и придумывать её нельзя.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import table_exists

revision: str = "025_day_wins"
down_revision: Union[str, None] = "024_habit_cycle_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if table_exists("day_wins"):
        return
    op.create_table(
        "day_wins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False, server_default="tasks"),
        sa.Column("tasks_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recurring_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_day_wins_id", "day_wins", ["id"])
    # День уникален: одна строка на день. Индекс, а не CONSTRAINT, потому что
    # ровно так же таблицу собирает create_all по модели DayWin.
    op.create_index("ix_day_wins_day", "day_wins", ["day"], unique=True)


def downgrade() -> None:
    if not table_exists("day_wins"):
        return
    op.drop_index("ix_day_wins_day", table_name="day_wins")
    op.drop_index("ix_day_wins_id", table_name="day_wins")
    op.drop_table("day_wins")
