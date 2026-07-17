"""Отдельное поле deadline (DL) — крайний срок, не путать с due_date (фокус дня).

Revision ID: 007_task_deadline
Revises: 006_task_normalize
Create Date: 2026-07-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import add_column_if_missing

revision: str = "007_task_deadline"
down_revision: Union[str, None] = "006_task_normalize"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    add_column_if_missing(
        "tasks",
        sa.Column("deadline", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("deadline")
