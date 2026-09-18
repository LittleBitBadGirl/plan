"""Офлайн-синхронизация PWA: журнал действий и конфликты.

Revision ID: 011_offline_sync
Revises: 010_manager_feedback
Create Date: 2026-09-18

Идемпотентна: ``init_db`` выполняет ``create_all`` до миграций, поэтому таблицы
на чистой базе могут уже существовать.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import table_exists

revision: str = "011_offline_sync"
down_revision: Union[str, None] = "010_manager_feedback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ensure_tables() -> None:
    if not table_exists("offline_actions"):
        op.create_table(
            "offline_actions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_uuid", sa.String(length=64), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=True),
            sa.Column("payload", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="applied"),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            ),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            "ix_offline_actions_client_uuid",
            "offline_actions",
            ["client_uuid"],
            unique=True,
        )
        op.create_index("ix_offline_actions_task_id", "offline_actions", ["task_id"])

    if not table_exists("offline_conflicts"):
        op.create_table(
            "offline_conflicts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_uuid", sa.String(length=64), nullable=True),
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("task_title", sa.String(length=500), nullable=True),
            sa.Column("field", sa.String(length=32), nullable=False),
            sa.Column("base_value", sa.Text(), nullable=True),
            sa.Column("server_value", sa.Text(), nullable=True),
            sa.Column("local_value", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            ),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolution", sa.String(length=16), nullable=True),
        )
        op.create_index("ix_offline_conflicts_task_id", "offline_conflicts", ["task_id"])


def upgrade() -> None:
    _ensure_tables()


def downgrade() -> None:
    if table_exists("offline_conflicts"):
        op.drop_table("offline_conflicts")
    if table_exists("offline_actions"):
        op.drop_table("offline_actions")
