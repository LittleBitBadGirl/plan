"""Разбор карьерного капитала по периодам: career_reviews.

Revision ID: 023_career_reviews
Revises: 022_task_planned_for
Create Date: 2026-10-07

Карьерный капитал перестаёт быть списком переписанных задач: раз в месяц
генератор раскладывает закрытые задачи по активам и пишет по ним короткие
пункты, а страница показывает готовый снимок. Таблица хранит снимок целиком
(JSON в `payload`), поэтому страница ничего не считает на лету и не зависит
от того, что модель вернула в прошлый раз.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import table_exists

revision: str = "023_career_reviews"
down_revision: Union[str, None] = "022_task_planned_for"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if table_exists("career_reviews"):
        return
    op.create_table(
        "career_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("period_month", sa.String(length=7), nullable=False, unique=True),
        sa.Column("total_tasks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("generator", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Индекс отдельно не создаём: unique=True в колонке уже даёт уникальный индекс.


def downgrade() -> None:
    if table_exists("career_reviews"):
        op.drop_table("career_reviews")
