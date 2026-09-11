"""Обратная связь по менеджерам: таблицы managers и manager_feedback.

Revision ID: 010_manager_feedback
Revises: 009_perf_indexes
Create Date: 2026-09-11

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migration_utils import table_exists

revision: str = "010_manager_feedback"
down_revision: Union[str, None] = "009_perf_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ensure_tables() -> None:
    if not table_exists("managers"):
        op.create_table(
            "managers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("projects", sa.String(length=500), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            ),
        )

    if not table_exists("manager_feedback"):
        op.create_table(
            "manager_feedback",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("manager_id", sa.Integer(), sa.ForeignKey("managers.id"), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("period_month", sa.String(length=7), nullable=False),
            sa.Column("kind", sa.String(length=20), nullable=False, server_default="minus"),
            sa.Column("project", sa.String(length=200), nullable=True),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("links", sa.Text(), nullable=True),
            sa.Column("files", sa.Text(), nullable=True),
            sa.Column("source", sa.String(length=20), nullable=False, server_default="web"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            ),
        )
        op.create_index(
            "ix_manager_feedback_manager",
            "manager_feedback",
            ["manager_id", "period_month"],
        )


def _seed_managers() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(*) FROM managers")).scalar()
    if count:
        return
    op.execute(
        sa.text(
            """
            INSERT INTO managers (name, projects, is_active, sort_order)
            VALUES
                ('Алёна Савченко', 'Атол, Майоли, Содис, АИЖ', 1, 1),
                ('Герман Лышков', 'СберМаркетинг (Б24)', 1, 2)
            """
        )
    )


def upgrade() -> None:
    _ensure_tables()
    _seed_managers()


def downgrade() -> None:
    if table_exists("manager_feedback"):
        op.drop_table("manager_feedback")
    if table_exists("managers"):
        op.drop_table("managers")
