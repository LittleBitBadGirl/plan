"""Архив отметок цикла: колонка is_archival в period_entries.

Старые отметки (например, перенесённые из другого приложения) помечаются
is_archival=1 и не участвуют в средних, текущем цикле и календаре дашборда —
показываются отдельным блоком «Архив» на странице /cycle.

Revision ID: 027_period_archive
Revises: 026_retelling_books
Create Date: 2026-10-07

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import add_column_if_missing

revision: str = "027_period_archive"
down_revision: Union[str, None] = "026_retelling_books"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    add_column_if_missing(
        "period_entries",
        sa.Column("is_archival", sa.Boolean, server_default="0"),
    )


def downgrade() -> None:
    with op.batch_alter_table("period_entries") as batch_op:
        batch_op.drop_column("is_archival")
