"""Исторические ALTER и data-fix (раньше в init_db).

Revision ID: 002_legacy_schema
Revises: 001_baseline
Create Date: 2026-06-04

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import add_column_if_missing

revision: str = "002_legacy_schema"
down_revision: Union[str, None] = "001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    add_column_if_missing(
        "categories",
        sa.Column("type", sa.String(length=20), server_default="task"),
    )
    add_column_if_missing("tasks", sa.Column("tags", sa.String(length=500), nullable=True))
    add_column_if_missing("tasks", sa.Column("impact_notes", sa.Text(), nullable=True))
    add_column_if_missing(
        "tasks",
        sa.Column("is_milestone", sa.Boolean(), server_default=sa.text("0")),
    )
    add_column_if_missing("tasks", sa.Column("estimated_minutes", sa.Integer(), nullable=True))
    add_column_if_missing("tasks", sa.Column("actual_minutes", sa.Integer(), nullable=True))
    add_column_if_missing(
        "tasks",
        sa.Column("item_kind", sa.String(length=20), server_default="task"),
    )
    add_column_if_missing(
        "shopping_items",
        sa.Column("is_archived", sa.Boolean(), server_default=sa.text("0")),
    )
    add_column_if_missing(
        "shopping_items",
        sa.Column("item_kind", sa.String(length=20), server_default="purchase"),
    )
    add_column_if_missing(
        "recurring_tasks",
        sa.Column("missed_count", sa.Integer(), server_default=sa.text("0")),
    )
    add_column_if_missing(
        "calendar_events",
        sa.Column("is_all_day", sa.Boolean(), server_default=sa.text("0")),
    )
    add_column_if_missing(
        "calendar_events",
        sa.Column("calendar_source", sa.String(length=20), server_default="yandex"),
    )
    add_column_if_missing(
        "calendar_events",
        sa.Column("calendar_kind", sa.String(length=20), server_default="work"),
    )

    op.execute(sa.text("UPDATE tasks SET item_kind = 'task' WHERE item_kind IS NULL"))
    op.execute(
        sa.text("UPDATE shopping_items SET item_kind = 'purchase' WHERE item_kind IS NULL")
    )
    op.execute(
        sa.text(
            "UPDATE shopping_items SET is_archived = 1 "
            "WHERE is_purchased = 1 AND (is_archived IS NULL OR is_archived = 0)"
        )
    )
    op.execute(
        sa.text(
            "UPDATE calendar_events SET calendar_source = 'yandex' "
            "WHERE calendar_source IS NULL OR calendar_source = ''"
        )
    )
    op.execute(
        sa.text(
            "UPDATE calendar_events SET calendar_kind = 'work' "
            "WHERE calendar_kind IS NULL OR calendar_kind = ''"
        )
    )
    op.execute(
        sa.text(
            "UPDATE calendar_events SET external_uid = 'yandex:' || external_uid "
            "WHERE external_uid NOT LIKE 'yandex:%' AND external_uid NOT LIKE 'google:%'"
        )
    )


def downgrade() -> None:
    pass
