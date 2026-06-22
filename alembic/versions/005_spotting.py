"""Добавление is_spotting в period_entries для отслеживания мазни.

Мазня (spotting) — дни с незначительными выделениями до или после месячных.
Не учитывается в avg_period, но видна в статистике для врача.

Revision ID: 005_spotting
Revises: 004_hermes_reports
Create Date: 2026-06-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import add_column_if_missing

revision: str = "005_spotting"
down_revision: Union[str, None] = "004_hermes_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    add_column_if_missing(
        "period_entries",
        sa.Column("is_spotting", sa.Boolean, server_default="0"),
    )


def downgrade() -> None:
    with op.batch_alter_table("period_entries") as batch_op:
        batch_op.drop_column("is_spotting")
