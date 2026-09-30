"""Мероприятия: таблица events.

Revision ID: 020_events
Revises: 019_habit_log_cycle_unique
Create Date: 2026-09-30

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import table_exists

revision: str = "020_events"
down_revision: Union[str, None] = "019_habit_log_cycle_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if table_exists("events"):
        return
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("url", sa.String(length=1000), nullable=True),
        sa.Column("image_url", sa.String(length=1000), nullable=True),
        sa.Column("image_file", sa.String(length=300), nullable=True),
        sa.Column("location", sa.String(length=500), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("start_time", sa.String(length=5), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="none"),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="web"),
        sa.Column("is_archived", sa.Integer(), nullable=False, server_default="0"),
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
    op.create_index("ix_events_id", "events", ["id"])
    op.create_index("ix_events_start_date", "events", ["start_date"])
    op.create_index("ix_events_range", "events", ["start_date", "end_date"])
    op.create_index("ix_events_status", "events", ["status"])


def downgrade() -> None:
    if table_exists("events"):
        op.drop_table("events")
