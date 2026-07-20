"""Performance indexes for dashboard hot path.

Revision ID: 009_perf_indexes
Revises: 008_portfolio_analyzer
Create Date: 2026-07-20

"""

from typing import Sequence, Union

from alembic import op

revision: str = "009_perf_indexes"
down_revision: Union[str, None] = "008_portfolio_analyzer"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_tasks_dashboard_day",
        "tasks",
        ["due_date", "is_archived", "parent_task_id", "status"],
    )
    op.create_index(
        "ix_habit_logs_habit_cycle",
        "habit_logs",
        ["habit_id", "cycle_number"],
    )
    op.create_index("ix_tasks_completed_at", "tasks", ["completed_at"])


def downgrade() -> None:
    op.drop_index("ix_tasks_completed_at", table_name="tasks")
    op.drop_index("ix_habit_logs_habit_cycle", table_name="habit_logs")
    op.drop_index("ix_tasks_dashboard_day", table_name="tasks")
