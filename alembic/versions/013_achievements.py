"""Достижения: таблица achievements (ручные записи, две полки).

Revision ID: 013_achievements
Revises: 012_task_overdue_since
Create Date: 2026-09-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import table_exists

revision: str = "013_achievements"
down_revision: Union[str, None] = "012_task_overdue_since"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if table_exists("achievements"):
        return
    op.create_table(
        "achievements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("sphere", sa.String(length=20), nullable=False, server_default="personal"),
        sa.Column("tag", sa.String(length=100), nullable=True),
        sa.Column("happened_on", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
    )
    op.create_index("ix_achievements_sphere", "achievements", ["sphere"])


def downgrade() -> None:
    if table_exists("achievements"):
        op.drop_table("achievements")
