"""Поля для нормализации задачи кроном: suggested_title + suggested_due_date.

Крон-категоризатор предлагает «чистый» текст задачи (без служебной шелухи)
и вынесенную из текста дату. Применяются ТОЛЬКО по кнопке подтверждения
в Telegram — крон ничего не меняет в тексте/дате молча.

Revision ID: 006_task_normalize
Revises: 005_spotting
Create Date: 2026-06-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import add_column_if_missing

revision: str = "006_task_normalize"
down_revision: Union[str, None] = "005_spotting"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    add_column_if_missing(
        "tasks",
        sa.Column("suggested_title", sa.String(500), nullable=True),
    )
    add_column_if_missing(
        "tasks",
        sa.Column("suggested_due_date", sa.Date, nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("suggested_due_date")
        batch_op.drop_column("suggested_title")
