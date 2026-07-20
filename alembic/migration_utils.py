"""Утилиты для идемпотентных миграций SQLite."""

import sqlalchemy as sa
from alembic import op


def table_exists(table: str) -> bool:
    bind = op.get_bind()
    return table in sa.inspect(bind).get_table_names()


def column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def add_column_if_missing(table: str, column: sa.Column) -> None:
    if column_exists(table, column.name):
        return
    with op.batch_alter_table(table) as batch_op:
        batch_op.add_column(column)
