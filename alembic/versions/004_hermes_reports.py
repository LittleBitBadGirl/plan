"""Добавление source и status в ai_reports для интеграции с Hermes.

source: 'deepseek' (старый AI) или 'hermes' (новый data-driven)
status: 'pending' (Hermes ещё не обработал) или 'done'

Revision ID: 004_hermes_reports
Revises: 003_task_size
Create Date: 2026-06-12

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import add_column_if_missing

revision: str = "004_hermes_reports"
down_revision: Union[str, None] = "003_task_size"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    add_column_if_missing(
        "ai_reports",
        sa.Column("source", sa.String(length=20), server_default="deepseek"),
    )
    add_column_if_missing(
        "ai_reports",
        sa.Column("status", sa.String(length=20), server_default="done"),
    )
    # Существующие записи помечаем как deepseek/done
    op.execute(sa.text("UPDATE ai_reports SET source = 'deepseek', status = 'done' WHERE source IS NULL"))


def downgrade() -> None:
    with op.batch_alter_table("ai_reports") as batch_op:
        batch_op.drop_column("status")
        batch_op.drop_column("source")
