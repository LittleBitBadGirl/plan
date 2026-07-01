"""Добавление поля size для задач (L/XL — крупные и очень крупные).

Revision ID: 003_task_size
Revises: 002_legacy_schema
Create Date: 2026-06-12

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import add_column_if_missing

revision: str = "003_task_size"
down_revision: Union[str, None] = "002_legacy_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    add_column_if_missing(
        "tasks",
        sa.Column("size", sa.String(length=4), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("size")
