"""Baseline: схема из SQLAlchemy-моделей (create_all в init_db).

Существующие БД до Alembic можно отметить без DDL:
  alembic stamp 001_baseline

Revision ID: 001_baseline
Revises:
Create Date: 2026-06-04

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """DDL таблиц — в app.db.database.init_db (Base.metadata.create_all)."""
    pass


def downgrade() -> None:
    pass
