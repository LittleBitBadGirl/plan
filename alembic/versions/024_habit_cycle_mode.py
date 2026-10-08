"""Режим цикла трекера: habits.cycle_mode.

Revision ID: 024_habit_cycle_mode
Revises: 023_career_reviews
Create Date: 2026-10-08

У трекера появляется выбор на создании: «на N дней» или «непрерывный».
Раньше выбора не было — все трекеры жили циклами по 30 дней, и продолжать
их приходилось кнопкой «След. 30 дней». Теперь режим хранится в колонке:

  days    — цикл ровно target_days дней, кончился — Вера решает: продлить или архив;
  monthly — непрерывный, цикл это календарный месяц, номер цикла растёт сам.

Бэкфилл намеренно не делается: у всех существующих трекеров остаётся `days`.
Их циклы уже отмерены по 30 дней, история отметок к ним привязана по номеру
цикла — перевод такого трекера в месячные сдвинул бы сетки прошлых циклов.
server_default закрывает и старые строки, и INSERT-ы мимо ORM.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import column_exists, table_exists

revision: str = "024_habit_cycle_mode"
down_revision: Union[str, None] = "023_career_reviews"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if not table_exists("habits"):
        return
    if not column_exists("habits", "cycle_mode"):
        op.add_column(
            "habits",
            sa.Column(
                "cycle_mode",
                sa.String(length=16),
                nullable=False,
                server_default="days",
            ),
        )


def downgrade() -> None:
    if not table_exists("habits"):
        return
    if column_exists("habits", "cycle_mode"):
        op.drop_column("habits", "cycle_mode")
